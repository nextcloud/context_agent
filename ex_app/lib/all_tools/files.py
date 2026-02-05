# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import Nextcloud
import niquests

from ex_app.lib.all_tools.lib.files import get_file_id_from_file_url

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def get_file_content(file_path: str):
		"""
		Get the content of a file
		:param file_path: the path of the file
		:return: 
		"""

		user_id = nc.ocs('GET', '/ocs/v2.php/cloud/user')["id"]
		
		response = nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{file_path}", headers={
			"Content-Type": "application/json",
		})

		return response.text

	@tool
	@safe_tool
	def get_file_content_by_file_link(file_url: str):
		"""
		Get the content of a file given an internal Nextcloud link (e.g., https://host/index.php/f/12345)
		:param file_url: the internal file URL
		:return:
		"""

		file_id = get_file_id_from_file_url(file_url)
		# Generate a direct download link using the fileId
		info = nc.ocs('POST', '/ocs/v2.php/apps/dav/api/v1/direct', json={'fileId': file_id}, response_type='json')
		download_url = info.get('url') if isinstance(info, dict) else None

		if not download_url:
			raise Exception('Could not generate download URL from file id')

		# Download the file from the direct download URL
		response = niquests.get(download_url)

		return response.text

	@tool
	@safe_tool
	def get_folder_tree(depth: int):
		"""
		Get the folder tree of the user
		:param depth: the depth of the returned folder tree
		:return: 
		"""

		return nc.ocs('GET', '/ocs/v2.php/apps/files/api/v1/folder-tree', json={'depth': depth}, response_type='json')

	@tool
	@dangerous_tool
	def create_public_sharing_link(path: str):
		"""
		Creates a public sharing link for a file or folder
		:param path: the path of the file or folder
		:return: 
		"""

		response = nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
					'path': path,
					'shareType': 3,
				})

		return response

	return [
		get_file_content,
		get_file_content_by_file_link,
		get_folder_tree,
		create_public_sharing_link,
	]

def get_category_name():
	return "Files"

def is_available(nc: Nextcloud):
	return True