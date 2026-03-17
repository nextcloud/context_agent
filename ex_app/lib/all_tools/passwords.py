# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_password_folders():
		"""
		List all password folders
		:return: list of folders with their id, label, and parent folder
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/folder/list", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def search_passwords(search_term: str):
		"""
		Search for password entries by keyword (searches in labels and urls, NOT in actual passwords)
		:param search_term: text to search for in password labels and URLs
		:return: list of matching password entries (without the actual password values)
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/password/list", headers={
			"Content-Type": "application/json",
		})
		all_passwords = response.json()

		# Filter passwords by search term in label or url
		matching = [
			{
				'id': p.get('id'),
				'label': p.get('label'),
				'username': p.get('username'),
				'url': p.get('url'),
				'folder': p.get('folder'),
				'tags': p.get('tags', [])
			}
			for p in all_passwords
			if search_term.lower() in p.get('label', '').lower()
			or search_term.lower() in p.get('url', '').lower()
		]

		return matching

	@tool
	@dangerous_tool
	async def get_password_by_id(password_id: str):
		"""
		Retrieve a password entry including the actual password value (requires user confirmation)
		:param password_id: the id of the password entry (obtainable via search_passwords)
		:return: complete password entry including the password value
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/password/show", headers={
			"Content-Type": "application/json",
		}, params={'id': password_id})

		return response.json()

	@tool
	@dangerous_tool
	async def create_password(label: str, username: str, password: str, url: Optional[str] = None, notes: Optional[str] = None, folder_id: Optional[str] = None):
		"""
		Create a new password entry
		:param label: label/name for the password entry
		:param username: username for the entry
		:param password: the password to store
		:param url: optional URL associated with this password
		:param notes: optional notes/description
		:param folder_id: optional folder id to organize the password (obtainable via list_password_folders)
		:return: the created password entry
		"""
		notes_with_ai_note = f"{notes or ''}\n\nCreated by Nextcloud AI Assistant."

		payload = {
			'label': label,
			'username': username,
			'password': password,
			'notes': notes_with_ai_note
		}
		if url:
			payload['url'] = url
		if folder_id:
			payload['folder'] = folder_id

		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/password/create", headers={
			"Content-Type": "application/json",
		}, json=payload)

		return response.json()

	@tool
	@dangerous_tool
	async def update_password(password_id: str, label: Optional[str] = None, username: Optional[str] = None, password: Optional[str] = None, url: Optional[str] = None, notes: Optional[str] = None):
		"""
		Update an existing password entry
		:param password_id: the id of the password to update (obtainable via search_passwords)
		:param label: new label/name
		:param username: new username
		:param password: new password
		:param url: new URL
		:param notes: new notes
		:return: the updated password entry
		"""
		# First get the current password entry
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/password/show", headers={
			"Content-Type": "application/json",
		}, params={'id': password_id})
		current = response.json()

		# Build update payload with current values as defaults
		payload = {
			'id': password_id,
			'label': label if label is not None else current.get('label'),
			'username': username if username is not None else current.get('username'),
			'password': password if password is not None else current.get('password'),
			'url': url if url is not None else current.get('url'),
			'notes': notes if notes is not None else current.get('notes'),
			'folder': current.get('folder')
		}

		response = await nc._session._create_adapter(True).request('PATCH', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/password/update", headers={
			"Content-Type": "application/json",
		}, json=payload)

		return response.json()

	@tool
	@dangerous_tool
	async def delete_password(password_id: str):
		"""
		Delete a password entry
		:param password_id: the id of the password to delete (obtainable via search_passwords)
		:return: confirmation of deletion
		"""
		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/password/delete", headers={
			"Content-Type": "application/json",
		}, params={'id': password_id})

		return response.json()

	@tool
	@dangerous_tool
	async def create_password_folder(label: str, parent_folder_id: Optional[str] = None):
		"""
		Create a new password folder for organization
		:param label: name for the folder
		:param parent_folder_id: optional parent folder id to create a subfolder (obtainable via list_password_folders)
		:return: the created folder
		"""
		payload = {
			'label': label
		}
		if parent_folder_id:
			payload['parent'] = parent_folder_id

		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/passwords/api/1.0/folder/create", headers={
			"Content-Type": "application/json",
		}, json=payload)

		return response.json()

	return [
		list_password_folders,
		search_passwords,
		get_password_by_id,
		create_password,
		update_password,
		delete_password,
		create_password_folder
	]

def get_category_name():
	return "Passwords"

async def is_available(nc: AsyncNextcloudApp):
	return 'passwords' in await nc.capabilities
