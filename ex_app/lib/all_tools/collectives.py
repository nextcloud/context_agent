# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from urllib.parse import quote
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):

	async def _user_id() -> str:
		return (await nc.ocs('GET', '/ocs/v2.php/cloud/user'))['id']

	async def _page_webdav_url(user_id: str, page: dict) -> str:
		# A page's markdown file lives at:
		#   /remote.php/dav/files/{user}/{collectivePath}/{filePath}/{fileName}
		# filePath is empty for top-level pages.
		parts = [page['collectivePath'], page.get('filePath') or '', page['fileName']]
		encoded = [quote(p) for p in parts if p]
		return f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{'/'.join(encoded)}"

	# --- Collectives ---

	@tool
	@safe_tool
	async def list_collectives():
		"""
		List all Collectives (wiki-like knowledge bases) the current user is a member of.
		Each collective contains pages of Markdown content, organized in a tree.
		:return: list of collectives with id, name, emoji, slug, and the user's permissions (canEdit, canShare)
		"""
		return json.dumps(await nc.ocs('GET', '/ocs/v2.php/apps/collectives/api/v1.0/collectives'))

	# --- Pages (read) ---

	@tool
	@safe_tool
	async def list_collective_pages(collective_id: int):
		"""
		List all pages in a Collective as a flat list with tree information.
		Pages form a tree via parentId (0 = top-level / landing page). Each page has an id needed
		by every other page tool, a title, and metadata (emoji, tags, last editor, trashed status).
		Markdown content is not included - fetch it with get_page_content.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:return: list of pages with id, title, emoji, parentId, subpageOrder, tags, lastUserId, timestamp, size, trashTimestamp
		"""
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages'))

	@tool
	@safe_tool
	async def get_page(collective_id: int, page_id: int):
		"""
		Get metadata for a single Collectives page (without the markdown body).
		Use get_page_content for the markdown body.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page (obtainable with list_collective_pages)
		:return: page metadata including title, emoji, parentId, subpageOrder, tags, lastUserId, timestamp, size
		"""
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}'))

	@tool
	@safe_tool
	async def get_page_content(collective_id: int, page_id: int):
		"""
		Get the Markdown content of a Collectives page.
		Fetches the underlying .md file via WebDAV. Returns an empty string for pages that have
		never been written to (newly created pages materialize their file on first write).
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page (obtainable with list_collective_pages)
		:return: the markdown content of the page, or empty string if the file has not been written yet
		"""
		page_resp = await nc.ocs('GET', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}')
		page = page_resp['page'] if isinstance(page_resp, dict) and 'page' in page_resp else page_resp
		user_id = await _user_id()
		url = await _page_webdav_url(user_id, page)
		response = await nc._session._create_adapter(True).request('GET', url, headers={
			'Content-Type': 'application/json',
		})
		if response.status_code == 404:
			return ''
		return response.text

	@tool
	@safe_tool
	async def list_page_trash(collective_id: int):
		"""
		List trashed pages in a Collective. Trashed pages can be restored with restore_page or
		removed permanently with delete_page_permanently. Trashed pages are eventually removed by
		a background job after an admin-configured retention period.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:return: list of trashed pages with id, title, trashTimestamp, parentId, and the rest of the page metadata
		"""
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/trash'))

	# --- Pages (write) ---

	@tool
	@dangerous_tool
	async def create_page(collective_id: int, parent_id: int, title: str):
		"""
		Create a new page in a Collective as a child of an existing page.
		Use parent_id = landing page id (from list_collective_pages, the page with parentId=0) for
		a top-level page. The page is created with an empty body; call update_page_content afterward
		to write markdown.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param parent_id: the id of the parent page (obtainable with list_collective_pages)
		:param title: the title for the new page
		:return: the created page's metadata including its id
		"""
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{parent_id}', json={
			'title': title,
		}))

	@tool
	@dangerous_tool
	async def update_page_content(collective_id: int, page_id: int, content: str):
		"""
		Overwrite the Markdown content of a Collectives page.
		Replaces the entire page body. To append, first read with get_page_content and concatenate.
		If another user has the page open in the real-time editor, their session may overwrite this
		write on save - consider rename_page or trash_page for destructive intent instead.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page (obtainable with list_collective_pages)
		:param content: the new markdown body for the page (replaces existing content)
		:return: success confirmation with the page id
		"""
		page_resp = await nc.ocs('GET', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}')
		page = page_resp['page'] if isinstance(page_resp, dict) and 'page' in page_resp else page_resp
		user_id = await _user_id()
		url = await _page_webdav_url(user_id, page)
		await nc._session._create_adapter(True).request('PUT', url, headers={
			'Content-Type': 'text/markdown',
		}, data=content)
		return json.dumps({'status': 'success', 'page_id': page_id})

	@tool
	@dangerous_tool
	async def rename_page(collective_id: int, page_id: int, title: str):
		"""
		Change the title of a Collectives page. Also renames the underlying .md file on disk.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page (obtainable with list_collective_pages)
		:param title: the new title
		:return: the updated page metadata
		"""
		return json.dumps(await nc.ocs('PUT', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}', json={
			'title': title,
		}))

	@tool
	@dangerous_tool
	async def move_page(collective_id: int, page_id: int, parent_id: int):
		"""
		Move a page under a different parent within the same collective.
		Use parent_id = landing page id to move the page to top-level.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page to move (obtainable with list_collective_pages)
		:param parent_id: the id of the new parent page (obtainable with list_collective_pages)
		:return: the updated page metadata
		"""
		return json.dumps(await nc.ocs('PUT', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}', json={
			'parentId': parent_id,
		}))

	@tool
	@dangerous_tool
	async def set_page_emoji(collective_id: int, page_id: int, emoji: str):
		"""
		Set or clear the emoji icon for a Collectives page.
		The emoji is displayed in the page tree and title bar. Pass an empty string to clear.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page (obtainable with list_collective_pages)
		:param emoji: a single emoji character (e.g. "📝"), or empty string to clear
		:return: the updated page metadata
		"""
		return json.dumps(await nc.ocs('PUT', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}/emoji', json={
			'emoji': emoji,
		}))

	@tool
	@dangerous_tool
	async def trash_page(collective_id: int, page_id: int):
		"""
		Soft-delete a page by moving it to the collective's page trash.
		Trashed pages can be restored with restore_page until a background job purges them after
		the admin-configured retention period. Use delete_page_permanently on a trashed page to
		remove it immediately.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the page (obtainable with list_collective_pages)
		:return: the trashed page metadata with trashTimestamp set
		"""
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}'))

	@tool
	@dangerous_tool
	async def restore_page(collective_id: int, page_id: int):
		"""
		Restore a previously trashed page back to the collective.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the trashed page (obtainable with list_page_trash)
		:return: the restored page metadata with trashTimestamp cleared
		"""
		return json.dumps(await nc.ocs('PATCH', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/trash/{page_id}'))

	@tool
	@dangerous_tool
	async def delete_page_permanently(collective_id: int, page_id: int):
		"""
		Permanently delete a page that is already in the trash. This cannot be undone.
		To delete a live page, call trash_page first, then this tool.
		:param collective_id: the id of the collective (obtainable with list_collectives)
		:param page_id: the id of the trashed page (obtainable with list_page_trash)
		:return: confirmation of permanent deletion
		"""
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/trash/{page_id}'))

	return [
		list_collectives,
		list_collective_pages,
		get_page,
		get_page_content,
		list_page_trash,
		create_page,
		update_page_content,
		rename_page,
		move_page,
		set_page_emoji,
		trash_page,
		restore_page,
		delete_page_permanently,
	]


def get_category_name():
	return "Collectives"


async def is_available(nc: AsyncNextcloudApp):
	try:
		await nc.ocs('GET', '/ocs/v2.php/apps/collectives/api/v1.0/collectives')
	except:
		return False
	return True
