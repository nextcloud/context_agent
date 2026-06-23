# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import niquests
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.files.files_async import AsyncFilesAPI, FsNode

from ex_app.lib.all_tools.lib.decorator import dangerous_tool, safe_tool
from ex_app.lib.all_tools.lib.files import get_file_id_from_file_url


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def get_file_content(file_path: str):
		"""
		Get the content of a nextcloud-internal file of the current user
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
		:param file_url: the nextcloud-internal file URL
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

	def __format_fs_node(fsnode: FsNode) -> dict:
		# todo: permissions info
		return {
			'path': fsnode.user_path,
			'file_id': fsnode.info.fileid,
			'etag': fsnode.etag.replace('"', '').replace("'", ''),
			'bytes': fsnode.info.size,
			'creation_date': fsnode.info.creation_date.isoformat(),
			'last_modified': fsnode.info.last_modified.isoformat(),
			'mimetype': fsnode.info.mimetype,
			'is_shared': fsnode.is_shared,
			'is_favourite': fsnode.info.favorite,
			'is_version': fsnode.info.is_version,
			'trash_info': {
				'in_trash': fsnode.info.in_trash,
				**({
					'trashbin_filename': fsnode.info.trashbin_filename,
					'original_location': fsnode.info.trashbin_original_location,
					'deletion_time': fsnode.info.trashbin_deletion_time,
				} if fsnode.info.in_trash else {}),
			},
			'lock_info': {
				'is_locked': fsnode.lock_info.is_locked,
				**({
					'owner': fsnode.lock_info.owner,
					'owner_display_name': fsnode.lock_info.owner_display_name,
					'type': fsnode.lock_info.type.name,
					'creation_time': fsnode.lock_info.lock_creation_time,
					'ttl': fsnode.lock_info.lock_ttl,
					'locked_by_app': fsnode.lock_info.owner_editor,
				} if fsnode.lock_info.is_locked else {}),
			},
		}


	@tool
	@safe_tool
	async def get_file_tree(path: str = '/', include_metadata = False, depth: int = 1):
		"""
		Get the file tree of the user (lists the folders and files the user has in Nextcloud Files)
		:param path: the path to enumerate. It should be relative to the root directory like /Media and NOT /userid/files/Media
		:param include_metadata: include the etag, file/folder id, last modified times, etc. with the file/folder paths
		:param depth: how many directory levels should be included in output. Default = 1 (only specified directory). Max depth = 5.
		:return:
		"""

		files_handle = AsyncFilesAPI(nc._session)
		fsnode_list = await files_handle.listdir(path, min(5, depth))
		if include_metadata:
			return [__format_fs_node(fsnode) for fsnode in fsnode_list]

		return [fsnode.user_path for fsnode in fsnode_list]

	@tool
	@safe_tool
	async def get_folder_tree(depth: int):
		"""
		Get the folder tree of the user (lists only the folders the user has in Nextcloud Files)
		:param depth: the depth of the returned folder tree
		:return:
		"""

		return await nc.ocs('GET', '/ocs/v2.php/apps/files/api/v1/folder-tree', params={'depth': depth}, response_type='json')

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
		}, data=content)

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

	return [
		get_file_content,
		get_file_content_by_file_link,
		get_file_tree,
		get_folder_tree,
		create_public_sharing_link,
		upload_file,
		create_folder,
		move_file,
		copy_file,
		delete_file
	]

def get_category_name():
	return "Files"

async def is_available(nc: AsyncNextcloudApp):
	return True