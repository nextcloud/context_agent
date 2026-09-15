# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, destructive, impulse

# Nextcloud Circles member type constants
TYPE_USER = 1
TYPE_GROUP = 2
TYPE_MAIL = 4
TYPE_CONTACT = 8
TYPE_CIRCLE = 16
TYPE_APP = 10000


def _validate_circle_id(circle_id: str) -> str:
	if '/' in circle_id or '\\' in circle_id:
		raise ValueError(f'Invalid circle id: {circle_id!r}')
	return circle_id


def _validate_member_id(member_id: str) -> str:
	if '/' in member_id or '\\' in member_id:
		raise ValueError(f'Invalid member id: {member_id!r}')
	return member_id


async def get_tools(nc: AsyncNextcloudApp):

	def new_member_radius(member_type=TYPE_USER):
		"""Adding a group or another team to a team pulls in everyone in it, not just one person."""
		if member_type in (TYPE_GROUP, TYPE_CIRCLE):
			return ImpulseRadius.GROUP
		if member_type == TYPE_MAIL:
			return ImpulseRadius.EXTERNAL
		return ImpulseRadius.INDIVIDUALS

	async def removed_member_radius(circle_id, member_id):
		"""What a member loses when they are taken out of a team.

		Nobody gains anything, but the access being withdrawn is exactly as wide as
		adding that member was: a single account loses a team, a group or a nested
		team loses it for everybody in it.
		"""
		_validate_circle_id(circle_id)
		_validate_member_id(member_id)
		members = await nc.ocs('GET', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members')
		for member in members or []:
			if member_id in (member.get('id'), member.get('singleId')):
				return new_member_radius(member.get('userType'))
		raise ValueError(f'No member {member_id!r} in team {circle_id!r}')

	async def circle_radius(circle_id):
		"""Who a team already reaches, read off the members it already has.

		Changing a team's name or description changes what every member of it sees,
		so the audience of the change is the membership -- which is nobody at all
		while the user is still the only one in it.
		"""
		_validate_circle_id(circle_id)
		members = await nc.ocs('GET', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members')
		user_id = await nc.user
		radius = ImpulseRadius.SELF
		for member in members or []:
			member_type = member.get('userType')
			if member_type in (TYPE_GROUP, TYPE_CIRCLE, TYPE_MAIL):
				# A group, a nested team or a mail address: reuse the reach that adding
				# such a member would have had.
				radius = max(radius, new_member_radius(member_type))
			elif member.get('userId') != user_id:
				# Anyone else in the team makes this a team rather than a private note.
				radius = max(radius, ImpulseRadius.GROUP)
		return radius

	@tool
	@impulse(ImpulseRadius.SELF)
	async def list_circles():
		"""
		List all circles (teams) the user is a member of
		:return: list of circles with their id, name, and description
		"""
		return json.dumps(await nc.ocs('GET', '/ocs/v2.php/apps/circles/circles'))

	@tool
	@impulse(ImpulseRadius.SELF)
	async def get_circle_details(circle_id: str):
		"""
		Get detailed information about a specific circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:return: complete circle information including members
		"""
		_validate_circle_id(circle_id)
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/circles/circles/{circle_id}'))

	@tool
	@impulse(ImpulseRadius.SELF)
	async def list_circle_members(circle_id: str):
		"""
		List all members of a specific circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:return: list of members with their id, display name, type, and level
		"""
		_validate_circle_id(circle_id)
		circle_members = await nc.ocs('GET', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members')
		return json.dumps(circle_members)

	@tool
	@impulse(ImpulseRadius.SELF)
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
	@impulse(new_member_radius)
	async def add_member_to_circle(circle_id: str, member_id: str, member_type: int = TYPE_USER):
		"""
		Add a member to a circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:param member_id: the user id, email, group name, or circle id to add
		:param member_type: type of member as integer constant - 1=user, 2=group, 4=mail, 8=contact, 16=circle (default: 1 for user)
		:return: the added member information
		"""
		_validate_circle_id(circle_id)
		payload = {
			"members": [{
				'id': member_id,
				'type': member_type
			}],
		}
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members/multi', json=payload))

	@tool
	@impulse(removed_member_radius)
	@destructive
	async def remove_member_from_circle(circle_id: str, member_id: str):
		"""
		Remove a member from a circle (team)
		:param circle_id: the id of the circle (obtainable via list_circles)
		:param member_id: the id of the member to remove (obtainable via list_circle_members)
		:return:
		"""
		_validate_circle_id(circle_id)
		_validate_member_id(member_id)
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/circles/circles/{circle_id}/members/{member_id}'))

	@tool
	@impulse(circle_radius)
	async def update_circle(circle_id: str, name: Optional[str] = None, description: Optional[str] = None):
		"""
		Update circle (team) information
		:param circle_id: the id of the circle to update (obtainable via list_circles)
		:param name: new name for the circle
		:param description: new description
		:return:
		"""
		_validate_circle_id(circle_id)
		if name is not None:
			await nc.ocs('PUT', f'/ocs/v2.php/apps/circles/circles/{circle_id}/name', json={'value': name})
		if description is not None:
			await nc.ocs('PUT', f'/ocs/v2.php/apps/circles/circles/{circle_id}/description', json={'value': description})
		return

	@tool
	@impulse(ImpulseRadius.SELF)
	@destructive
	async def delete_circle(circle_id: str):
		"""
		Delete a circle (team)
		:param circle_id: the id of the circle to delete (obtainable via list_circles)
		:return:
		"""
		_validate_circle_id(circle_id)
		await nc.ocs('DELETE', f'/ocs/v2.php/apps/circles/circles/{circle_id}')

	@tool
	@impulse(ImpulseRadius.GROUP)
	async def share_with_circle(path: str, circle_id: str, permissions: int = 19):
		"""
		Share a file or folder with a circle (team)
		:param path: the path of the file or folder to share
		:param circle_id: the id of the circle to share with (obtainable via list_circles)
		:param permissions: permissions bitmask - 1=read, 2=update, 4=create, 8=delete, 16=share. Default is 19
		:return: the created share
		"""
		_validate_circle_id(circle_id)
		return json.dumps(await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json={
			'path': path,
			'shareType': 7,  # 7 = circle
			'shareWith': circle_id,
			'permissions': permissions
		}))

	return [
		list_circles,
		get_circle_details,
		list_circle_members,
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
