# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
import niquests

from ex_app.lib.all_tools.lib.files import get_file_id_from_file_url

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def get_file_content(file_path: str):
		"""
		Get the content of a file
		:param file_path: the path of the file
		:return:
		"""

		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{file_path}", headers={
			"Content-Type": "application/json",
		})

		return response.text

	@tool
	@safe_tool
	async def get_file_content_by_file_link(file_url: str):
		"""
		Get the content of a file given an internal Nextcloud link (e.g., https://host/index.php/f/12345)
		:param file_url: the internal file URL
		:return: text content of the file
		"""

		file_id = get_file_id_from_file_url(file_url)
		# Generate a direct download link using the fileId
		info = await nc.ocs('POST', '/ocs/v2.php/apps/dav/api/v1/direct', json={'fileId': file_id}, response_type='json')
		download_url = info.get('ocs', {}).get('data', {}).get('url', None)

		if not download_url:
			raise Exception('Could not generate download URL from file id')

		# Download the file from the direct download URL
		response = await niquests.async_api.get(download_url)

		return response.text

	@tool
	@safe_tool
	async def get_folder_tree(depth: int):
		"""
		Get the folder tree of the user (lists the files the user has in Nextcloud Files)
		:param depth: the depth of the returned folder tree
		:return:
		"""

		return await nc.ocs('GET', '/ocs/v2.php/apps/files/api/v1/folder-tree', json={'depth': depth}, response_type='json')

	@tool
	@dangerous_tool
	async def create_public_sharing_link(path: str):
		"""
		Creates a public sharing link for a file or folder
		:param path: the path of the file or folder
		:return:
		"""

		response = await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
					'path': path,
					'shareType': 3,
				})

		return response

	@tool
	@dangerous_tool
	async def upload_file(path: str, content: str):
		"""
		Upload or create a new file with text content
		:param path: the path where the file should be created (e.g., "/Documents/myfile.txt")
		:param content: the text content to write to the file
		:return: success confirmation
		"""
		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		response = await nc._session._create_adapter(True).request('PUT', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{path}", headers={
			"Content-Type": "text/plain",
		}, content=content)

		return {"status": "success", "path": path}

	@tool
	@dangerous_tool
	async def create_folder(path: str):
		"""
		Create a new folder
		:param path: the path of the folder to create (e.g., "/Documents/NewFolder")
		:return: success confirmation
		"""
		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		response = await nc._session._create_adapter(True).request('MKCOL', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{path}", headers={
			"Content-Type": "application/json",
		})

		return {"status": "success", "path": path}

	@tool
	@dangerous_tool
	async def move_file(source_path: str, destination_path: str):
		"""
		Move or rename a file or folder
		:param source_path: the current path of the file/folder
		:param destination_path: the new path for the file/folder
		:return: success confirmation
		"""
		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		response = await nc._session._create_adapter(True).request('MOVE', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{source_path}", headers={
			"Destination": f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{destination_path}",
		})

		return {"status": "success", "from": source_path, "to": destination_path}

	@tool
	@dangerous_tool
	async def copy_file(source_path: str, destination_path: str):
		"""
		Copy a file or folder
		:param source_path: the path of the file/folder to copy
		:param destination_path: the destination path
		:return: success confirmation
		"""
		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		response = await nc._session._create_adapter(True).request('COPY', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{source_path}", headers={
			"Destination": f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{destination_path}",
		})

		return {"status": "success", "from": source_path, "to": destination_path}

	@tool
	@dangerous_tool
	async def delete_file(path: str):
		"""
		Delete a file or folder
		:param path: the path of the file/folder to delete
		:return: success confirmation
		"""
		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{path}", headers={
			"Content-Type": "application/json",
		})

		return {"status": "success", "deleted": path}

	@tool
	@dangerous_tool
	async def add_file_tag(file_id: int, tag_name: str):
		"""
		Add a tag to a file
		:param file_id: the file id
		:param tag_name: the tag to add
		:return: success confirmation
		"""
		# First, ensure the tag exists
		await nc.ocs('POST', '/ocs/v2.php/apps/files/api/v1/tags', json={'name': tag_name})

		# Then assign it to the file
		return await nc.ocs('POST', f'/ocs/v2.php/apps/files/api/v1/files/{file_id}/tags/{tag_name}')

	@tool
	@safe_tool
	async def search_files_by_tag(tag_name: str):
		"""
		Search for files by tag
		:param tag_name: the tag to search for
		:return: list of files with that tag
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/files/api/v1/tags/{tag_name}/files')

	@tool
	@safe_tool
	async def list_file_tags():
		"""
		List all available file tags
		:return: list of tags
		"""
		return await nc.ocs('GET', '/ocs/v2.php/apps/files/api/v1/tags')

	return [
		get_file_content,
		get_file_content_by_file_link,
		get_folder_tree,
		create_public_sharing_link,
		upload_file,
		create_folder,
		move_file,
		copy_file,
		delete_file,
		add_file_tag,
		search_files_by_tag,
		list_file_tags
	]

def get_category_name():
	return "Files"

async def is_available(nc: AsyncNextcloudApp):
	return True