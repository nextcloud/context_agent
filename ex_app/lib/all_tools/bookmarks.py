# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_bookmarks(page: int = 0, limit: int = 100, folder_id: Optional[int] = None, tags: Optional[list[str]] = None):
		"""
		List bookmarks with optional filtering
		:param page: page number for pagination (starts at 0)
		:param limit: number of bookmarks per page (default 100)
		:param folder_id: filter by folder id (obtainable via list_bookmark_folders)
		:param tags: filter by tags - list of tag names
		:return: list of bookmarks with url, title, description, and tags
		"""
		params = {
			'page': page,
			'limit': limit
		}
		if folder_id is not None:
			params['folder'] = folder_id
		if tags:
			params['tags[]'] = tags

		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/bookmark", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, params=params)
		return response.json()

	@tool
	@safe_tool
	async def search_bookmarks(search_term: str):
		"""
		Search for bookmarks by keyword
		:param search_term: text to search for in bookmark titles, urls, and descriptions
		:return: list of matching bookmarks
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/bookmark", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, params={'search': search_term})
		return response.json()

	@tool
	@dangerous_tool
	async def create_bookmark(url: str, title: Optional[str] = None, description: Optional[str] = None, tags: Optional[list[str]] = None, folder_id: Optional[int] = None):
		"""
		Create a new bookmark
		:param url: the URL to bookmark
		:param title: title for the bookmark (auto-detected if not provided)
		:param description: optional description
		:param tags: list of tags to add to the bookmark
		:param folder_id: folder to place the bookmark in (obtainable via list_bookmark_folders)
		:return: the created bookmark
		"""
		description_with_ai_note = f"{description or ''}\n\nBookmarked by Nextcloud AI Assistant."

		payload = {
			'url': url,
			'description': description_with_ai_note
		}
		if title:
			payload['title'] = title
		if tags:
			payload['tags'] = tags
		if folder_id is not None:
			payload['folders'] = [folder_id]

		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/bookmark", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json=payload)
		return response.json()

	@tool
	@dangerous_tool
	async def update_bookmark(bookmark_id: int, url: Optional[str] = None, title: Optional[str] = None, description: Optional[str] = None, tags: Optional[list[str]] = None):
		"""
		Update an existing bookmark
		:param bookmark_id: the id of the bookmark to update (obtainable via list_bookmarks)
		:param url: new URL
		:param title: new title
		:param description: new description
		:param tags: new list of tags (replaces existing tags)
		:return: the updated bookmark
		"""
		payload = {}
		if url is not None:
			payload['url'] = url
		if title is not None:
			payload['title'] = title
		if description is not None:
			payload['description'] = description
		if tags is not None:
			payload['tags'] = tags

		response = await nc._session._create_adapter(True).request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/bookmark/{bookmark_id}", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json=payload)
		return response.json()

	@tool
	@dangerous_tool
	async def delete_bookmark(bookmark_id: int):
		"""
		Delete a bookmark
		:param bookmark_id: the id of the bookmark to delete (obtainable via list_bookmarks)
		:return: confirmation of deletion
		"""
		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/bookmark/{bookmark_id}", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})
		return response.json()

	@tool
	@safe_tool
	async def list_bookmark_folders():
		"""
		List all bookmark folders
		:return: list of folders with their id, title, and parent folder
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/folder", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def create_bookmark_folder(title: str, parent_folder_id: Optional[int] = None):
		"""
		Create a new bookmark folder
		:param title: name for the folder
		:param parent_folder_id: optional parent folder id to create a subfolder (obtainable via list_bookmark_folders)
		:return: the created folder
		"""
		payload = {
			'title': title
		}
		if parent_folder_id is not None:
			payload['parent_folder'] = parent_folder_id

		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/folder", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json=payload)
		return response.json()

	@tool
	@safe_tool
	async def list_bookmark_tags():
		"""
		List all bookmark tags with usage counts
		:return: list of tags with the number of bookmarks using each tag
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/bookmarks/public/rest/v2/tag", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})
		return response.json()

	return [
		list_bookmarks,
		search_bookmarks,
		create_bookmark,
		update_bookmark,
		delete_bookmark,
		list_bookmark_folders,
		create_bookmark_folder,
		list_bookmark_tags
	]

def get_category_name():
	return "Bookmarks"

async def is_available(nc: AsyncNextcloudApp):
	return 'bookmarks' in await nc.capabilities
