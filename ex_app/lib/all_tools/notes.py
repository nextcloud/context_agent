# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_notes():
		"""
		List all notes in the Nextcloud Notes app
		:return: a list of notes with their id, title, category, and preview
		"""
		return await nc.ocs('GET', '/ocs/v2.php/apps/notes/api/v1/notes')

	@tool
	@safe_tool
	async def get_note_content(note_id: int):
		"""
		Get the full content of a specific note
		:param note_id: the id of the note to retrieve (obtainable via list_notes)
		:return: the full note content including title, content, category, and metadata
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/notes/api/v1/notes/{note_id}')

	@tool
	@dangerous_tool
	async def create_note(title: str, content: str, category: Optional[str] = None):
		"""
		Create a new note in the Nextcloud Notes app
		:param title: the title of the note
		:param content: the content of the note
		:param category: optional category/folder for the note
		:return: the created note with its id and metadata
		"""
		content_with_ai_note = f"{content}\n\n---\n\nThis note was created by Nextcloud AI Assistant."

		payload = {
			'title': title,
			'content': content_with_ai_note
		}
		if category:
			payload['category'] = category

		return await nc.ocs('POST', '/ocs/v2.php/apps/notes/api/v1/notes', json=payload)

	@tool
	@dangerous_tool
	async def update_note(note_id: int, title: Optional[str] = None, content: Optional[str] = None, category: Optional[str] = None):
		"""
		Update an existing note
		:param note_id: the id of the note to update (obtainable via list_notes)
		:param title: new title for the note (optional)
		:param content: new content for the note (optional)
		:param category: new category for the note (optional)
		:return: the updated note
		"""
		payload = {}
		if title is not None:
			payload['title'] = title
		if content is not None:
			payload['content'] = content
		if category is not None:
			payload['category'] = category

		return await nc.ocs('PUT', f'/ocs/v2.php/apps/notes/api/v1/notes/{note_id}', json=payload)

	@tool
	@dangerous_tool
	async def delete_note(note_id: int):
		"""
		Delete a note
		:param note_id: the id of the note to delete (obtainable via list_notes)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/notes/api/v1/notes/{note_id}')

	@tool
	@safe_tool
	async def search_notes(search_term: str):
		"""
		Search for notes containing a specific term
		:param search_term: the text to search for in note titles and content
		:return: list of matching notes
		"""
		all_notes = await nc.ocs('GET', '/ocs/v2.php/apps/notes/api/v1/notes')
		matching_notes = [
			note for note in all_notes
			if search_term.lower() in note.get('title', '').lower()
			or search_term.lower() in note.get('content', '').lower()
		]
		return matching_notes

	return [
		list_notes,
		get_note_content,
		create_note,
		update_note,
		delete_note,
		search_notes
	]

def get_category_name():
	return "Notes"

async def is_available(nc: AsyncNextcloudApp):
	return 'notes' in await nc.capabilities
