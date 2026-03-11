# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_music_artists(limit: int = 50):
		"""
		List all artists in the music library
		:param limit: maximum number of artists to return (default 50)
		:return: list of artists with their id and name
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/artists", headers={
			"Content-Type": "application/json",
		}, params={'limit': limit})
		return response.json()

	@tool
	@safe_tool
	async def list_music_albums(artist_id: Optional[int] = None, limit: int = 50):
		"""
		List albums in the music library, optionally filtered by artist
		:param artist_id: optional artist id to filter albums (obtainable via list_music_artists)
		:param limit: maximum number of albums to return (default 50)
		:return: list of albums
		"""
		params = {'limit': limit}
		if artist_id is not None:
			params['artist'] = artist_id

		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/albums", headers={
			"Content-Type": "application/json",
		}, params=params)
		return response.json()

	@tool
	@safe_tool
	async def list_music_tracks(album_id: Optional[int] = None, artist_id: Optional[int] = None, limit: int = 100):
		"""
		List tracks in the music library with optional filtering
		:param album_id: optional album id to filter tracks (obtainable via list_music_albums)
		:param artist_id: optional artist id to filter tracks (obtainable via list_music_artists)
		:param limit: maximum number of tracks to return (default 100)
		:return: list of tracks
		"""
		params = {'limit': limit}
		if album_id is not None:
			params['album'] = album_id
		if artist_id is not None:
			params['artist'] = artist_id

		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/tracks", headers={
			"Content-Type": "application/json",
		}, params=params)
		return response.json()

	@tool
	@safe_tool
	async def search_music(search_term: str):
		"""
		Search for music by artist, album, or track name
		:param search_term: text to search for
		:return: search results with matching artists, albums, and tracks
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/search", headers={
			"Content-Type": "application/json",
		}, params={'query': search_term})
		return response.json()

	@tool
	@safe_tool
	async def list_music_playlists():
		"""
		List all music playlists
		:return: list of playlists with their id, name, and track count
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def get_playlist_tracks(playlist_id: int):
		"""
		Get all tracks in a playlist
		:param playlist_id: the id of the playlist (obtainable via list_music_playlists)
		:return: list of tracks in the playlist
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists/{playlist_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def create_music_playlist(name: str):
		"""
		Create a new music playlist
		:param name: name for the playlist
		:return: the created playlist
		"""
		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists", headers={
			"Content-Type": "application/json",
		}, json={
			'name': name
		})
		return response.json()

	@tool
	@dangerous_tool
	async def add_track_to_playlist(playlist_id: int, track_id: int):
		"""
		Add a track to a playlist
		:param playlist_id: the id of the playlist (obtainable via list_music_playlists)
		:param track_id: the id of the track to add (obtainable via list_music_tracks or search_music)
		:return: confirmation
		"""
		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists/{playlist_id}/add", headers={
			"Content-Type": "application/json",
		}, json={
			'trackIds': [track_id]
		})
		return response.json()

	@tool
	@dangerous_tool
	async def remove_track_from_playlist(playlist_id: int, track_index: int):
		"""
		Remove a track from a playlist
		:param playlist_id: the id of the playlist (obtainable via list_music_playlists)
		:param track_index: the position/index of the track in the playlist (0-based)
		:return: confirmation
		"""
		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists/{playlist_id}/remove", headers={
			"Content-Type": "application/json",
		}, json={
			'indices': [track_index]
		})
		return response.json()

	@tool
	@dangerous_tool
	async def delete_music_playlist(playlist_id: int):
		"""
		Delete a music playlist
		:param playlist_id: the id of the playlist to delete (obtainable via list_music_playlists)
		:return: confirmation of deletion
		"""
		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists/{playlist_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def rename_music_playlist(playlist_id: int, new_name: str):
		"""
		Rename a music playlist
		:param playlist_id: the id of the playlist to rename (obtainable via list_music_playlists)
		:param new_name: new name for the playlist
		:return: the updated playlist
		"""
		response = await nc._session._create_adapter(True).request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/music/api/playlists/{playlist_id}", headers={
			"Content-Type": "application/json",
		}, json={
			'name': new_name
		})
		return response.json()

	return [
		list_music_artists,
		list_music_albums,
		list_music_tracks,
		search_music,
		list_music_playlists,
		get_playlist_tracks,
		create_music_playlist,
		add_track_to_playlist,
		remove_track_from_playlist,
		delete_music_playlist,
		rename_music_playlist
	]

def get_category_name():
	return "Music"

async def is_available(nc: AsyncNextcloudApp):
	return 'music' in await nc.capabilities
