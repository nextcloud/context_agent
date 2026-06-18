# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import json
import logging
import os
from urllib.parse import quote, unquote

import niquests
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api._exceptions import NextcloudExceptionNotFound
from nc_py_api.files.files_async import AsyncFilesAPI
from pydantic import BaseModel, ValidationError, computed_field, field_validator

from ex_app.lib.all_tools.lib.decorator import safe_tool
from ex_app.lib.all_tools.lib.task_processing import run_task
from ex_app.lib.logger import log

# The agent only looks at the memory files inside the Memories folder and considers that
# the root folder, the paths exchanged with it are all relative to that.

CONTEXT_CHAT_SEARCH_TASK_TYPE = 'context_chat:context_chat_search'
FILES_PROVIDER_ID = 'files__default'
MEMORIES_RELATIVE_FOLDER_PATH = 'Context Agent/Memories'  # inside the user's Assistant folder
MAX_MEMORY_FOLDER_DEPTH = 2


class AgentFacingError(Exception):
	"""This exception's message would be returned to the agent to understand it's mistake"""
	...


class SourceItem(BaseModel):
	id: str  # source_id, in the form "appId__providerId: itemId"
	label: str
	icon: str
	url: str

	@computed_field
	@property
	def file_id(self) -> int:
		if not self.id.startswith(f'{FILES_PROVIDER_ID}: '):
			raise ValueError(f'Source id does not start with expected prefix: {self.id}')

		try:
			return int(self.id[len(f'{FILES_PROVIDER_ID}: '):])
		except ValueError as e:
			raise ValueError(
				f'Invalid source id format for extracting file_id: {self.id}'
			) from e


class SourcesList(BaseModel):
	# {"id":"files__default: <fileid>","label":"string","icon":"string","url":"string"}
	sources: list[SourceItem]

	@field_validator('sources', mode='before')
	@classmethod
	def parse_sources(cls, v: list) -> list:
		result = []
		for item in v:
			if isinstance(item, str):
				try:
					item = json.loads(item)
				except json.JSONDecodeError as e:
					raise ValueError(f'Invalid JSON in sources list: {item!r}') from e
			result.append(item)
		return result


async def __is_context_chat_available(nc: AsyncNextcloudApp, memories_folder_path: str):
	tasktypes = (await nc.ocs('GET', '/ocs/v2.php/taskprocessing/tasktypes'))['types'].keys()
	return CONTEXT_CHAT_SEARCH_TASK_TYPE in tasktypes


def __validate_memory_path(path: str, memories_folder_path: str, *, allow_folder_path: bool = False) -> tuple[str, str]:
	"""returns tuple[full_path, memories_scoped_path]"""
	if not path:
		raise AgentFacingError('Memory path cannot be empty')

	# decode URL-encoded paths (including double-encoding) to catch obfuscated traversal attempts
	decoded = unquote(unquote(unquote(path)))

	for candidate in (path, decoded):
		if '\x00' in candidate:
			raise RuntimeError('Memory path contains null byte')

		if '/..' in candidate or '../' in candidate or '..\\' in candidate:
			raise RuntimeError('Agent tried to access directories beyond the memories folder')

	if not allow_folder_path and (not decoded.endswith('.md') and not decoded.endswith('.markdown')):
		raise AgentFacingError('Memory file should be a markdown file')

	path_parts = [p for p in decoded.strip('/').split('/') if p]
	if len(path_parts) > MAX_MEMORY_FOLDER_DEPTH + 1:  # +1 for the filename itself
		raise AgentFacingError(f'Memory path exceeds maximum depth of {MAX_MEMORY_FOLDER_DEPTH}')

	# resolve the full absolute path and verify it stays within the memory folder
	full_path = os.path.normpath(os.path.join(memories_folder_path, decoded.lstrip('/')))

	if not full_path.startswith(memories_folder_path):
		raise RuntimeError('Agent tried to access directories beyond the memories folder')

	# url-safe path
	return (quote(full_path, safe='/'), decoded.lstrip('/'))


async def __create_folders_if_not_exists(nc: AsyncNextcloudApp, adapter: niquests.AsyncSession, memories_folder_path: str, user_id: str, scoped_path: str):
	"""
	Ensures all necessary folders exist for the given scoped_path (relative to the user's DAV root).
	First checks and recursively creates the base memories folder, then checks and creates
	any subfolders within it derived from scoped_path (excluding the filename).
	"""
	base_memories_path = quote(memories_folder_path, safe='/')

	# Ensure base memories folder exists
	propfind = await adapter.request(
		'PROPFIND',
		f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{base_memories_path}",
		headers={'Depth': '0'},
	)
	if propfind.status_code == 404:
		base_parts = [p for p in base_memories_path.split('/') if p]
		for i in range(1, len(base_parts) + 1):
			folder_path = '/'.join(base_parts[:i])
			r = await adapter.request('MKCOL', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{folder_path}")
			if r.status_code not in (200, 201, 405):  # 405 = already exists
				raise RuntimeError(f'Failed to create base memory folder {folder_path}: {r.status_code}')

	# Ensure subfolders inside the memories base folder exist (exclude the filename)
	scoped_parts = [p for p in scoped_path.split('/') if p]
	scoped_folder_parts = scoped_parts[:-1]
	if not scoped_folder_parts:
		return  # file is in the memories root, no subfolder needed

	full_subfolder_path = quote(f"{memories_folder_path}/{'/'.join(scoped_folder_parts)}", safe='/')
	propfind = await adapter.request(
		'PROPFIND',
		f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{full_subfolder_path}",
		headers={'Depth': '0'},
	)
	if propfind.status_code == 404:
		base_parts = [p for p in base_memories_path.split('/') if p]
		all_parts = base_parts + scoped_folder_parts
		for i in range(len(base_parts) + 1, len(all_parts) + 1):
			folder_path = '/'.join(all_parts[:i])
			r = await adapter.request('MKCOL', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{folder_path}")
			if r.status_code not in (200, 201, 405):  # 405 = already exists
				raise RuntimeError(f'Failed to create memory subfolder {folder_path}: {r.status_code}')


async def __get_assistant_folder_path(nc: AsyncNextcloudApp) -> str | None:
	try:
		res = await nc.ocs(
			'GET',
			'/ocs/v2.php/apps/assistant/api/v1/assistant-folder-path',
			response_type='json',
		)
	except NextcloudExceptionNotFound:
		return None
	except Exception as e:
		await log(
			nc,
			logging.WARNING,
			f"Failed to fetch Assistant's folder in user's home dir, user: {await nc.user}, exc: {e}",
		)
		return None

	# /<userId>/files/Assistant
	folder_path = res.get('ocs', {}).get('data', {}).get('path')

	if not folder_path or not isinstance(folder_path, str):
		return None

	return folder_path.removeprefix(f'/{await nc.user}/files/')


async def get_tools(nc: AsyncNextcloudApp):
	assistant_folder_path = await __get_assistant_folder_path(nc)
	memories_folder_path = f'{(assistant_folder_path or "").removesuffix("/")}/{MEMORIES_RELATIVE_FOLDER_PATH}'


	@tool
	@safe_tool
	async def list_memory_tree(depth: int = 2):
		"""
		Recursively list the memories stored in a file tree structure. Max depth is 2.
		:return: All the memory folders and filenames at the given depth as a newline-separated list of paths, e.g.:

			```
			/work/career_plans.md
			/work/projects/alpha_project.md
			/personal/hobbies
			/general_notes.md
			```
		"""

		files_handle = AsyncFilesAPI(nc._session)
		fsnode_list = await files_handle.listdir(
			path=memories_folder_path,
			depth=min(depth, MAX_MEMORY_FOLDER_DEPTH),
		)

		prefix = memories_folder_path.rstrip('/') + '/'

		paths = []
		for node in fsnode_list:
			scoped = node.user_path.removeprefix(prefix).rstrip('/')
			if not scoped:
				continue  # skip the root memories folder itself
			paths.append('/' + scoped)

		return '\n'.join(paths)

	@tool
	@safe_tool
	async def load_memory(path: str):
		"""
		Load one particular memory from the memory store identified by full path.
		:param path: The full file path of the memory separated by and started with a forward slash (/), and the file name should end in either .md or .markdown
		:return: The requested memory text
		"""
		try:
			full_path, _ = __validate_memory_path(path, memories_folder_path)
		except AgentFacingError as e:
			return {'error': str(e)}
		except Exception as e:
			await log(nc, logging.ERROR, f'Memory path validation failed: {e}')
			return {'error': 'Invalid memory path given'}

		# let the user-scoped request errors reach the agent, although it leaks the full path of the memory
		user_id = await nc.user
		response = await nc._session._create_adapter(True).request(
			'GET',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{full_path}",
			headers={"Content-Type": "application/json"},
		)
		return response.text

	@tool
	@safe_tool
	async def store_memory(path: str, content: str):
		"""
		Stores a complete memory file overwriting it if it already exists.
		This can also be used for editing memories but the full content of the memory must be passed.
		Memory is structured like a file system consisting of markdown files with a max depth of 2.
		The memory files are encouraged to be stored in a structured pattern so they are easy to find
			later on with folders and subfolders classifying them based on the subject matter, and
			the file/memory names indicating the contents of the memory.
		Good examples: "/work/career_plans.md", "/work/projects/alpha_project.md", "/personal/hobbies/reading_list.md", "/general_notes.md".
		Use lowercase names with underscores. Folder names should be broad categories (e.g. "work", "personal", "health").
		:param path: The full file path of the memory separated by and started with a forward slash (/), and the file name should end in either .md or .markdown
		:param content: The text content to store in the memory file.
		:return: Status of the operation.
		"""
		try:
			full_path, scoped_path = __validate_memory_path(path, memories_folder_path)
		except AgentFacingError as e:
			return {'error': str(e)}
		except Exception as e:
			await log(nc, logging.ERROR, f'Memory path validation failed: {e}')
			return {'error': 'Invalid memory path given'}

		user_id = await nc.user
		adapter = nc._session._create_adapter(True)

		# Ensure all parent folders exist
		await __create_folders_if_not_exists(nc, adapter, memories_folder_path, user_id, scoped_path)

		response = await adapter.request(
			'PUT',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{full_path}",
			headers={"Content-Type": "text/markdown"},
			data=content,
		)
		return {"status": "success", "path": path}

	@tool
	@safe_tool
	async def delete_memory(path: str):
		"""
		Deletes a particular memory file identified by a full file/memory path.
		:param path: The full file path of the memory separated by and started with a forward slash (/), and the file name should end in either .md or .markdown
		:return: Status of the deletion operation.
		"""
		if not path.endswith('.md') and not path.endswith('.markdown'):
			return {'error': (
				'Only individual memory files can be deleted using this tool.'
				' Use "delete_memory_folder" to delete entire folders/categories of memories.'
			)}

		try:
			full_path, _ = __validate_memory_path(path, memories_folder_path)
		except AgentFacingError as e:
			return {'error': str(e)}
		except Exception as e:
			await log(nc, logging.ERROR, f'Memory path validation failed: {e}')
			return {'error': 'Invalid memory path given'}

		user_id = await nc.user
		await nc._session._create_adapter(True).request(
			'DELETE',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{full_path}",
		)
		return {"status": "success", "path": path}

	@tool
	async def delete_memory_folder(path: str):
		"""
		Deletes the whole folder of memories by a full path.
		:param path: The full path of the folder separated by and started with a forward slash (/).
		:return: Status of the deletion operation.
		"""
		try:
			full_path, _ = __validate_memory_path(path, memories_folder_path, allow_folder_path=True)
		except AgentFacingError as e:
			return {'error': str(e)}
		except Exception as e:
			await log(nc, logging.ERROR, f'Memory path validation failed: {e}')
			return {'error': 'Invalid memory path given'}

		user_id = await nc.user
		await nc._session._create_adapter(True).request(
			'DELETE',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{full_path}",
		)
		return {"status": "success", "path": path}

	@tool
	@safe_tool
	async def search_memories(query: str, k: int = 5) -> list[dict[str, str]]:
		"""
		Do a semantic search over the contents of all the stored memories.
		:param query: The subject matter to search for
		:param k: No. of memories to return. Max 10 memories can be requested.
		:return: Top k matched memories and their paths
		"""

		if query == '':
			raise RuntimeError('Query must not be empty')

		files_handle = AsyncFilesAPI(nc._session)
		memories_folder_fsnode = await files_handle.by_path(memories_folder_path)

		task_input = {
			'prompt': query,
			'scopeType': 'source',
			'scopeList': [f'{FILES_PROVIDER_ID}: {memories_folder_fsnode.info.fileid}'],
			'scopeListMeta': '',
			'limit': min(k, 10),  # silent truncation
		}
		task_output = (await run_task(nc, CONTEXT_CHAT_SEARCH_TASK_TYPE, task_input)).output
		try:
			sources_list: SourcesList = SourcesList.model_validate(task_output)
		except ValidationError as e:
			raise RuntimeError(f'Malformed sources found in the output of Context Chat Search task: {e}')

		if sources_list.sources == []:
			raise RuntimeError('No memories found with the given query')

		prefix = memories_folder_path.rstrip('/') + '/'

		async def fetch(file_id: int) -> dict[str, str]:
			"""
			:return: {'path': string, 'content: string}
			"""
			fsnode = await files_handle.by_id(file_id)
			filepath = '/' + fsnode.user_path.removeprefix(prefix).rstrip('/')

			if fsnode is None:
				await log(nc, logging.WARNING, f'Could not fetch file by id: {file_id}')
				return {'path': filepath, 'content': ''}

			return {
				'path': filepath,
				'content': (await files_handle.download(fsnode)).decode(errors='replace'),
			}

		return await asyncio.gather(*[fetch(source.file_id) for source in sources_list.sources])

	return [
		*([
			list_memory_tree,
			load_memory,
			store_memory,
			delete_memory,
			delete_memory_folder,
			*([search_memories] if await __is_context_chat_available(nc, memories_folder_path) else []),
		] if assistant_folder_path else []),
	]


def get_category_name():
	return 'Memories'


async def is_available(nc: AsyncNextcloudApp):
	return True
