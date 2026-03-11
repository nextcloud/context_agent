# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_shares(path: Optional[str] = None, shared_with_me: bool = False):
		"""
		List all shares or shares for a specific file/folder
		:param path: optional path to list shares for a specific file/folder
		:param shared_with_me: if True, lists shares that others have shared with the current user
		:return: list of shares with details about permissions and share type
		"""
		params = {}
		if path:
			params['path'] = path
		if shared_with_me:
			params['shared_with_me'] = 'true'

		return await nc.ocs('GET', '/ocs/v2.php/apps/files_sharing/api/v1/shares', params=params)

	@tool
	@dangerous_tool
	async def share_with_user(path: str, share_with: str, permissions: int = 19):
		"""
		Share a file or folder with a user
		:param path: the path of the file or folder to share
		:param share_with: the user id to share with
		:param permissions: permissions bitmask - 1=read, 2=update, 4=create, 8=delete, 16=share. Default is 19 (read+update+delete+share)
		:return: the created share
		"""
		return await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
			'path': path,
			'shareType': 0,  # 0 = user
			'shareWith': share_with,
			'permissions': permissions
		})

	@tool
	@dangerous_tool
	async def share_with_group(path: str, share_with: str, permissions: int = 19):
		"""
		Share a file or folder with a group
		:param path: the path of the file or folder to share
		:param share_with: the group name to share with
		:param permissions: permissions bitmask - 1=read, 2=update, 4=create, 8=delete, 16=share. Default is 19 (read+update+delete+share)
		:return: the created share
		"""
		return await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
			'path': path,
			'shareType': 1,  # 1 = group
			'shareWith': share_with,
			'permissions': permissions
		})

	@tool
	@dangerous_tool
	async def update_share_permissions(share_id: int, permissions: int):
		"""
		Update permissions for an existing share
		:param share_id: the id of the share to update (obtainable via list_shares)
		:param permissions: new permissions bitmask - 1=read, 2=update, 4=create, 8=delete, 16=share
		:return: the updated share
		"""
		return await nc.ocs('PUT', f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}', json={
			'permissions': permissions
		})

	@tool
	@dangerous_tool
	async def delete_share(share_id: int):
		"""
		Remove/delete a share
		:param share_id: the id of the share to delete (obtainable via list_shares)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}')

	@tool
	@safe_tool
	async def list_user_groups():
		"""
		List all groups the current user belongs to
		:return: list of group names
		"""
		user_info = await nc.ocs('GET', '/ocs/v2.php/cloud/user')
		return user_info.get('groups', [])

	@tool
	@safe_tool
	async def get_share_info(share_id: int):
		"""
		Get detailed information about a specific share
		:param share_id: the id of the share (obtainable via list_shares)
		:return: share details including permissions, expiration, etc.
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}')

	return [
		list_shares,
		share_with_user,
		share_with_group,
		update_share_permissions,
		delete_share,
		list_user_groups,
		get_share_info
	]

def get_category_name():
	return "Sharing and Groups"

async def is_available(nc: AsyncNextcloudApp):
	return True
