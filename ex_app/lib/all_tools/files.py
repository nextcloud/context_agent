# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import xml.etree.ElementTree as ET
from urllib.parse import unquote

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

	@tool
	@safe_tool
	async def get_folder_tree(depth: int):
		"""
		Get the folder tree of the user (lists the files the user has in Nextcloud Files)
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
	@safe_tool
	async def get_file_id_by_path(file_path: str) -> int:
		"""
		Resolve a file or folder path to its Nextcloud file ID.
		:param file_path: the path of the file/folder (e.g., "/Documents/file.docx")
		:return: the numeric file ID
		"""
		user_id = await nc.user

		propfind_body = (
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
			'<d:prop><oc:fileid/></d:prop>'
			'</d:propfind>'
		)
		propfind_response = await nc._session._create_adapter(True).request(
			'PROPFIND',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{file_path.lstrip('/')}",
			headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "0"},
			data=propfind_body,
		)

		root = ET.fromstring(propfind_response.text)
		fileid_element = root.find(".//{http://owncloud.org/ns}fileid")
		if fileid_element is None or not fileid_element.text:
			raise Exception(f"Could not resolve file ID for path: {file_path}")
		return int(fileid_element.text)

	@tool
	@safe_tool
	async def get_file_path_by_id(file_id: int) -> str:
		"""
		Resolve a Nextcloud file ID to its path (relative to the user's files root).
		:param file_id: the numeric file ID
		:return: the path of the file/folder
		"""
		user_id = await nc.user

		search_body = (
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
			'<d:basicsearch>'
			'<d:select><d:prop><d:displayname/></d:prop></d:select>'
			f'<d:from><d:scope><d:href>/files/{user_id}</d:href><d:depth>infinity</d:depth></d:scope></d:from>'
			'<d:where>'
			'<d:eq>'
			'<d:prop><oc:fileid/></d:prop>'
			f'<d:literal>{file_id}</d:literal>'
			'</d:eq>'
			'</d:where>'
			'<d:orderby/>'
			'</d:basicsearch>'
			'</d:searchrequest>'
		)
		search_response = await nc._session._create_adapter(True).request(
			'SEARCH',
			f"{nc.app_cfg.endpoint}/remote.php/dav/",
			headers={"Content-Type": "text/xml; charset=utf-8"},
			data=search_body,
		)

		root = ET.fromstring(search_response.text)
		href_element = root.find(".//{DAV:}response/{DAV:}href")
		if href_element is None or not href_element.text:
			raise Exception(f"Could not resolve path for file ID: {file_id}")

		href = unquote(href_element.text)
		prefix = f"/remote.php/dav/files/{user_id}"
		if href.startswith(prefix):
			return href[len(prefix):] or "/"
		return href

	@tool
	@dangerous_tool
	async def convert_file(source_path: str, target_mime_type: str, destination_path: str | None = None):
		"""
		Convert a file from one MIME type to another (e.g., docx to pdf, jpg to png).
		Uses the Nextcloud files conversion API.
		:param source_path: the path of the file to convert (e.g., "/Documents/file.docx")
		:param target_mime_type: the target MIME type (e.g., "application/pdf")
		:param destination_path: optional destination path for the converted file
		:return: information about the converted file
		"""
		user_id = await nc.user

		propfind_body = (
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
			'<d:prop><oc:fileid/></d:prop>'
			'</d:propfind>'
		)
		propfind_response = await nc._session._create_adapter(True).request(
			'PROPFIND',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{source_path.lstrip('/')}",
			headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "0"},
			data=propfind_body,
		)

		root = ET.fromstring(propfind_response.text)
		fileid_element = root.find(".//{http://owncloud.org/ns}fileid")
		if fileid_element is None or not fileid_element.text:
			raise Exception(f"Could not resolve file ID for path: {source_path}")
		file_id = int(fileid_element.text)

		payload = {'fileId': file_id, 'targetMimeType': target_mime_type}
		if destination_path:
			payload['destination'] = destination_path

		return await nc.ocs(
			'POST',
			'/ocs/v2.php/apps/files/api/v1/convert',
			json=payload,
			response_type='json',
		)

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
		get_folder_tree,
		get_file_id_by_path,
		get_file_path_by_id,
		create_public_sharing_link,
		upload_file,
		create_folder,
		move_file,
		copy_file,
		convert_file,
		delete_file
	]

def get_category_name():
	return "Files"

async def is_available(nc: AsyncNextcloudApp):
	return True