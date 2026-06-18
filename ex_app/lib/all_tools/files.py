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
	async def get_folder_contents(folder_path: str):
		"""
		List the immediate contents (files and subfolders) of a folder in Nextcloud Files
		:param folder_path: the path of the folder (e.g., "/Documents" or "" for the root)
		:return: a list of items with name, path, type ("file" or "folder"), size, content_type and last_modified
		"""
		if '\x00' in folder_path or '\\' in folder_path:
			raise ValueError("Invalid folder path")

		segments = [s for s in folder_path.split('/') if s]
		for seg in segments:
			if seg == '..' or seg == '.':
				raise ValueError("Path traversal is not allowed in folder_path")

		user_id = (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))["id"]

		propfind_body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop>
	<d:displayname/>
	<d:resourcetype/>
	<d:getcontentlength/>
	<d:getcontenttype/>
	<d:getlastmodified/>
	<oc:fileid/>
  </d:prop>
</d:propfind>"""

		base = f"/remote.php/dav/files/{user_id}/"
		normalized = '/'.join(segments)
		url_path = base + (normalized + '/' if normalized else '')

		response = await nc._session._create_adapter(True).request('PROPFIND', f"{nc.app_cfg.endpoint}{url_path}", headers={
			"Content-Type": "application/xml; charset=utf-8",
			"Depth": "1",
		}, data=propfind_body)

		if response.status_code != 207:
			raise Exception(f"Error listing folder: {response.status_code} - {response.reason_phrase}")

		ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
		root = ET.fromstring(response.text)

		items = []
		for resp in root.findall("d:response", ns):
			href_el = resp.find("d:href", ns)
			if href_el is None or href_el.text is None:
				continue
			href = unquote(href_el.text)

			# Skip the folder itself; only return its children
			if href.rstrip('/') == url_path.rstrip('/'):
				continue

			# Defensive: ignore any entry the server returns outside our user's DAV root
			if not href.startswith(base):
				continue

			prop = resp.find("d:propstat/d:prop", ns)
			if prop is None:
				continue

			is_folder = prop.find("d:resourcetype/d:collection", ns) is not None
			rel_path = href[len(base):].rstrip('/') if is_folder else href[len(base):]
			name = rel_path.rsplit('/', 1)[-1]

			size_el = prop.find("d:getcontentlength", ns)
			ctype_el = prop.find("d:getcontenttype", ns)
			mod_el = prop.find("d:getlastmodified", ns)
			fileid_el = prop.find("oc:fileid", ns)

			items.append({
				"name": name,
				"path": "/" + rel_path,
				"type": "folder" if is_folder else "file",
				"size": int(size_el.text) if size_el is not None and size_el.text else None,
				"content_type": ctype_el.text if ctype_el is not None else None,
				"last_modified": mod_el.text if mod_el is not None else None,
				"file_id": int(fileid_el.text) if fileid_el is not None and fileid_el.text else None,
			})

		return items

	@tool
	@safe_tool
	async def get_folder_tree(depth: int):
		"""
		Get the folder tree of the user (lists the folders the user has in Nextcloud Files)
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
		get_folder_contents,
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