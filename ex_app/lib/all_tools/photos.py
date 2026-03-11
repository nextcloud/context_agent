# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_photo_albums():
		"""
		List all photo albums
		:return: list of albums with their id, name, and metadata
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def get_album_photos(album_id: str):
		"""
		Get all photos in an album
		:param album_id: the id of the album (obtainable via list_photo_albums)
		:return: list of photos in the album
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums/{album_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def create_photo_album(name: str):
		"""
		Create a new photo album
		:param name: name for the album
		:return: the created album
		"""
		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums", headers={
			"Content-Type": "application/json",
		}, json={
			'name': name
		})
		return response.json()

	@tool
	@dangerous_tool
	async def rename_photo_album(album_id: str, new_name: str):
		"""
		Rename a photo album
		:param album_id: the id of the album to rename (obtainable via list_photo_albums)
		:param new_name: new name for the album
		:return: the updated album
		"""
		response = await nc._session._create_adapter(True).request('PATCH', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums/{album_id}", headers={
			"Content-Type": "application/json",
		}, json={
			'name': new_name
		})
		return response.json()

	@tool
	@dangerous_tool
	async def delete_photo_album(album_id: str):
		"""
		Delete a photo album
		:param album_id: the id of the album to delete (obtainable via list_photo_albums)
		:return: confirmation of deletion
		"""
		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums/{album_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def add_photo_to_album(album_id: str, file_id: int):
		"""
		Add a photo to an album
		:param album_id: the id of the album (obtainable via list_photo_albums)
		:param file_id: the file id of the photo to add
		:return: confirmation
		"""
		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums/{album_id}/files", headers={
			"Content-Type": "application/json",
		}, json={
			'fileId': file_id
		})
		return response.json()

	@tool
	@dangerous_tool
	async def remove_photo_from_album(album_id: str, file_id: int):
		"""
		Remove a photo from an album
		:param album_id: the id of the album (obtainable via list_photo_albums)
		:param file_id: the file id of the photo to remove
		:return: confirmation
		"""
		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/photos/api/v1/albums/{album_id}/files/{file_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def search_photos_by_date(start_date: str, end_date: Optional[str] = None):
		"""
		Search for photos by date range (uses Nextcloud unified search for photos)
		:param start_date: start date in format YYYY-MM-DD
		:param end_date: optional end date in format YYYY-MM-DD (defaults to start_date)
		:return: list of photos taken in the date range
		"""
		search_term = f"{start_date}"
		if end_date and end_date != start_date:
			search_term = f"{start_date} to {end_date}"

		results = await nc.ocs('GET', '/ocs/v2.php/search/providers/photos/search', params={'term': search_term})
		return results.get('entries', [])

	return [
		list_photo_albums,
		get_album_photos,
		create_photo_album,
		rename_photo_album,
		delete_photo_album,
		add_photo_to_album,
		remove_photo_from_album,
		search_photos_by_date
	]

def get_category_name():
	return "Photos"

async def is_available(nc: AsyncNextcloudApp):
	return 'photos' in await nc.capabilities
