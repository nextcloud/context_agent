# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from fastmcp.server.dependencies import get_context
from langchain_core.tools import tool
from nc_py_api import Nextcloud

from typing import Optional

from ex_app.lib.all_tools.lib.context import get_nextcloud
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
		nonlocal nc
		nc = get_nextcloud(nc)

		user_id = nc.ocs('GET', '/ocs/v2.php/cloud/user')["id"]
		
		response = nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{file_path}", headers={
			"Content-Type": "application/json",
		})

		return response.text

	@tool
	@safe_tool
	def get_folder_tree(depth: int):
		"""
		Get the folder tree of the user
		:param depth: the depth of the returned folder tree
		:return: 
		"""
		nonlocal nc
		nc = get_nextcloud(nc)

		return nc.ocs('GET', '/ocs/v2.php/apps/files/api/v1/folder-tree', json={'depth': depth}, response_type='json')

	@tool
	@dangerous_tool
	def create_public_sharing_link(path: str):
		"""
		Creates a public sharing link for a file or folder
		:param path: the path of the file or folder
		:return: 
		"""
		nonlocal nc
		nc = get_nextcloud(nc)

		response = nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
					'path': path,
					'shareType': 3,
				})

		return response

	return [
		get_file_content,
		get_folder_tree,
		create_public_sharing_link,
	]

def get_category_name():
	return "Files"

def is_available(nc: Nextcloud):
	return True