# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.audience import share_id_radius
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, Reversibility, destructive, impulse, item_count, read_only, reversibility


async def get_tools(nc: AsyncNextcloudApp):

	async def existing_share_radius(share_id):
		"""Look the share up to see who it already grants access to."""
		return await share_id_radius(nc, share_id)

	async def share_deletion_reversibility(share_id):
		"""A share with a user, group, team or Talk conversation can be made again just
		like it was. A link or mail share cannot: a new one gets a new token, and the
		old link stays dead."""
		share = await nc.ocs('GET', f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}')
		if isinstance(share, list):
			share = share[0] if share else {}
		try:
			share_type = int(share.get('share_type'))
		except (TypeError, ValueError):
			return Reversibility.IRREVERSIBLE
		# 0 = user, 1 = group, 7 = team (circle), 10 = Talk conversation
		if share_type in (0, 1, 7, 10):
			return Reversibility.REVERSIBLE
		return Reversibility.IRREVERSIBLE

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
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
	@impulse(ImpulseRadius.INDIVIDUALS)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
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
	@impulse(ImpulseRadius.GROUP)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
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
	@impulse(existing_share_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
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
	@impulse(existing_share_radius)
	@destructive
	@reversibility(share_deletion_reversibility)
	@item_count(1)
	async def delete_share(share_id: int):
		"""
		Remove/delete a share
		:param share_id: the id of the share to delete (obtainable via list_shares)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}')

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_user_groups():
		"""
		List all groups the current user belongs to
		:return: list of group names
		"""
		user_info = await nc.ocs('GET', '/ocs/v2.php/cloud/user')
		return user_info.get('groups', [])

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
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
