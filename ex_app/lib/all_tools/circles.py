# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_circles():
		"""
		List all circles (teams) the user is a member of
		:return: list of circles with their id, name, and description
		"""
		return json.dumps(await nc.ocs('GET', '/ocs/v2.php/apps/circles/circles'))

	@tool
	@safe_tool
	async def get_circle_details(circle_id: str):
		"""
		Get detailed information about a specific circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:return: complete circle information including members
		"""
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/circles/circles/{circle_id}'))

	@tool
	@dangerous_tool
	async def create_circle(name: str, description: Optional[str] = None, is_personal: bool = False):
		"""
		Create a new circle (team)
		:param name: name for the circle
		:param description: optional description
		:param is_personal: whether this is a personal circle (default: False for regular teams)
		:return: the created circle
		"""
		description_with_ai_note = f"{description or ''}\n\nCreated by Nextcloud AI Assistant."

		payload = {
			'name': name,
			'description': description_with_ai_note,
			'personal': is_personal
		}
		return json.dumps(await nc.ocs('POST', '/ocs/v2.php/apps/circles/circles', json=payload))

	@tool
	@dangerous_tool
	async def add_member_to_circle(circle_id: str, user_id: str, member_type: str = 'user'):
		"""
		Add a member to a circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:param user_id: the user id or email to add
		:param member_type: type of member - 'user', 'group', 'mail', or 'contact'
		:return: the added member information
		"""
		payload = {
			'userId': user_id,
			'type': member_type
		}
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members', json=payload))

	@tool
	@dangerous_tool
	async def remove_member_from_circle(circle_id: str, member_id: str):
		"""
		Remove a member from a circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:param member_id: the id of the member to remove (obtainable via get_circle_details)
		:return: confirmation of removal
		"""
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members/{member_id}'))

	@tool
	@dangerous_tool
	async def update_circle(circle_id: str, name: Optional[str] = None, description: Optional[str] = None):
		"""
		Update circle (team) information
		:param circle_id: the id of the circle to update (obtainable via list_circles)
		:param name: new name for the circle
		:param description: new description
		:return: the updated circle
		"""
		payload = {}
		if name is not None:
			payload['name'] = name
		if description is not None:
			payload['description'] = description

		return json.dumps(await nc.ocs('PUT', f'/ocs/v2.php/apps/circles/circles/{circle_id}', json=payload))

	@tool
	@dangerous_tool
	async def delete_circle(circle_id: str):
		"""
		Delete a circle (team)
		:param circle_id: the id of the circle to delete (obtainable via list_circles)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/circles/circles/{circle_id}')

	@tool
	@dangerous_tool
	async def share_with_circle(path: str, circle_id: str, permissions: int = 19):
		"""
		Share a file or folder with a circle (team)
		:param path: the path of the file or folder to share
		:param circle_id: the id of the circle to share with (obtainable via list_circles)
		:param permissions: permissions bitmask - 1=read, 2=update, 4=create, 8=delete, 16=share. Default is 19
		:return: the created share
		"""
		return json.dumps(await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
			'path': path,
			'shareType': 7,  # 7 = circle
			'shareWith': circle_id,
			'permissions': permissions
		}))

	return [
		list_circles,
		get_circle_details,
		create_circle,
		add_member_to_circle,
		remove_member_from_circle,
		update_circle,
		delete_circle,
		share_with_circle
	]

def get_category_name():
	return "Circles/Teams"

async def is_available(nc: AsyncNextcloudApp):
	return 'circles' in await nc.capabilities
