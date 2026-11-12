# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, Reversibility, destructive, destructive_if, impulse, item_count, read_only, reversibility

# Resolving a card to its board costs one request per board the user can see, so
# the map is kept per user and out here, rather than in the get_tools closure that
# timed_memoize drops every minute along with the tools themselves. Only the radius
# each board works out to is kept, never the board itself: the boards are large,
# and the radius is all anybody asks for.
CARD_BOARD_TTL = 60
# A miss is worth one crawl -- a card the agent just created is not in the map
# yet. Right after a crawl it is not: an id that is simply not there would
# otherwise walk every board again on every call that mentions it.
CARD_BOARD_MISS_INTERVAL = 10
_card_radii: dict[str, dict] = {}
# IComment::MAX_MESSAGE_LENGTH on the Nextcloud side.
COMMENT_MAX_LENGTH = 1000


def _forget_stale_card_radii(now: float) -> None:
	"""Drop the maps that the next call would refetch anyway.

	Nothing here is ever handed back to a user who stopped asking, so without this
	the process keeps a map per user that has ever used a Deck tool for as long as
	it runs. Sweeping on the way in costs a pass over a dict that is as long as the
	list of users active in the last minute.

	A map somebody is still crawling is left alone: dropping it would leave the
	crawl filling in a dict nobody reads any more, and send everyone waiting on it
	off to walk every board over again. A map that has never been filled in reads
	as infinitely old, so without this the first crawl of all would be swept out
	from under itself by the next call to arrive.
	"""
	stale = [
		user_id for user_id, cache in _card_radii.items()
		if now - cache['fetched_at'] > CARD_BOARD_TTL and cache['crawl'] is None
	]
	for user_id in stale:
		del _card_radii[user_id]


async def get_tools(nc: AsyncNextcloudApp):

	DECK_API = f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0"
	DECK_HEADERS = {"Content-Type": "application/json", "OCS-APIREQUEST": "true"}

	async def deck_request(method, path, payload=None):
		response = await nc._session._create_adapter().request(method, f"{DECK_API}{path}", headers=DECK_HEADERS, json=payload)
		if response.status_code >= 400:
			# Deck explains what went wrong in a JSON body; pass that on rather than
			# letting the caller trip over an error object where it expected a card.
			try:
				body = response.json()
			except ValueError:
				body = None
			message = body.get('message') if isinstance(body, dict) else None
			raise ValueError(f'Deck {method} {path} failed with HTTP {response.status_code}: {message or response.reason_phrase}')
		return response.json()

	async def list_live_boards(query=''):
		"""The boards of the user, without the deleted ones Deck still lists for undoing the deletion."""
		return [board for board in await deck_request('GET', f'/boards{query}') if not board.get('deletedAt')]

	async def board_acl_radius(board):
		"""Who a board is shared with, read off its access control list."""
		radius = ImpulseRadius.SELF
		for entry in board.get('acl') or []:
			# 0 = user, 1 = group, 7 = team (circle)
			radius = max(radius, ImpulseRadius.INDIVIDUALS if entry.get('type') == 0 else ImpulseRadius.GROUP)
		return radius

	async def board_radius(board_id):
		"""Look the board up to see who can already see what is on it."""
		# The board list carries the full access control list, no details=true needed.
		for board in await list_live_boards():
			if board.get('id') == int(board_id):
				return await board_acl_radius(board)
		raise ValueError(f'No board with id {board_id!r}')

	async def assignment_radius(board_id):
		"""Assigning reaches the assignee on top of whoever the board already reaches."""
		return max(ImpulseRadius.INDIVIDUALS, await board_radius(board_id))

	async def crawl_card_radii(cache):
		"""Map every card the user can reach to the radius of the board it lives on.

		One request for the board list, then one per board -- the stacks of a board
		carry its cards, so the cards themselves cost nothing extra. Each board is
		reduced to its radius as it goes past, so what stays behind is one small
		integer per card rather than the board it came from.
		"""
		try:
			radii = {}
			for board in await list_live_boards():
				radius = await board_acl_radius(board)
				for stack in await deck_request('GET', f"/boards/{board['id']}/stacks"):
					for card in stack.get('cards') or []:
						radii[card['id']] = radius
			cache['cards'] = radii
			cache['fetched_at'] = time.monotonic()
		finally:
			# Cleared even when the crawl failed, so the next call tries again rather
			# than waiting on a task that is never coming back.
			cache['crawl'] = None

	async def refresh_card_radii(cache):
		"""Fill the map in, or wait for the crawl that is already doing it.

		The pending tool calls of one turn are classified against each other, so a
		batch naming several cards the map has not seen yet arrives here all at once.
		Each of those would otherwise walk every board of its own, for the same
		answer. The first one to get here starts the crawl and the rest wait on it,
		shielded so that a caller giving up does not cancel the crawl the others are
		still waiting for.
		"""
		crawl = cache['crawl']
		if crawl is None:
			crawl = asyncio.ensure_future(crawl_card_radii(cache))
			# Set before the task gets to run: ensure_future only schedules it, and
			# there is no await between here and there for it to start in.
			cache['crawl'] = crawl
		await asyncio.shield(crawl)

	async def card_radius(card_id):
		"""A comment on a card reaches whoever the card's board reaches."""
		card_id = int(card_id)
		now = time.monotonic()
		_forget_stale_card_radii(now)
		# A map that has just been swept, or was never there, reads as infinitely old
		# and so gets fetched rather than being trusted while it is still empty.
		cache = _card_radii.setdefault(await nc.user, {'cards': {}, 'fetched_at': float('-inf'), 'crawl': None})
		age = now - cache['fetched_at']
		# At most one crawl per call, whether the map went stale or the card is new.
		if age > CARD_BOARD_TTL or (card_id not in cache['cards'] and age > CARD_BOARD_MISS_INTERVAL):
			await refresh_card_radii(cache)
		radius = cache['cards'].get(card_id)
		if radius is None:
			raise ValueError(f'No board holds a card with id {card_id!r}')
		return radius

	async def stack_size(board_id, stack_id):
		"""Deleting a stack takes every card on it along with it, archived ones included."""
		count = 1
		for path in (f'/boards/{int(board_id)}/stacks', f'/boards/{int(board_id)}/stacks/archived'):
			for stack in await deck_request('GET', path):
				if stack.get('id') == int(stack_id):
					count += len(stack.get('cards') or [])
		return count

	async def board_size(board_id):
		"""Sharing a board exposes every card on it, archived ones included."""
		count = 1
		for path in (f'/boards/{int(board_id)}/stacks', f'/boards/{int(board_id)}/stacks/archived'):
			for stack in await deck_request('GET', path):
				count += len(stack.get('cards') or [])
		return count

	async def find_user_acl(board_id, user_id):
		"""The entry of a board's access control list that shares it with the user, if any."""
		for board in await list_live_boards():
			if board.get('id') == int(board_id):
				for entry in board.get('acl') or []:
					participant = entry.get('participant') or {}
					if entry.get('type') == 0 and participant.get('uid') == user_id:
						return entry
				return None
		raise ValueError(f'No board with id {board_id!r}')

	def check_comment_length(message):
		"""Deck rejects comments over 1000 characters, on update with a bare HTTP 500."""
		if len(message) > COMMENT_MAX_LENGTH:
			raise ValueError(f'The comment is {len(message) - COMMENT_MAX_LENGTH} characters too long, including the note about the AI Assistant')

	async def card_update_overwrites(title=None, description=None, due_date=None):
		"""The title, description and due date it replaces are not kept anywhere; done can simply be toggled back."""
		return title is not None or description is not None or due_date is not None

	async def card_update_reversibility(title=None, description=None, due_date=None):
		if await card_update_overwrites(title, description, due_date):
			return Reversibility.IRREVERSIBLE
		return Reversibility.REVERSIBLE

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_boards():
		"""
		List all existing kanban boards available in the Nextcloud Deck app for the current user with their available info
		:return: a dictionary with all decks of the user
		"""
		return json.dumps(await list_live_boards('?details=true'))

	@tool
	# A new board is shared with nobody yet.
	@impulse(ImpulseRadius.SELF)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def create_board(title: str, color: str = '0082c9'):
		"""
		Create a new kanban board in the Nextcloud Deck app. The board starts out without any stacks; add them with create_stack.
		:param title: the title of the board
		:param color: optional color of the board as a hex code, e.g. 0082c9 (default)
		:return: the created board with its id
		"""
		color = color.lstrip('#')
		if len(color) != 6 or any(c not in '0123456789abcdefABCDEF' for c in color):
			raise ValueError(f'The color {color!r} is not a six-digit hex code')
		return json.dumps(await deck_request('POST', '/boards', {
			'title': title,
			'color': color,
		}))

	@tool
	@impulse(assignment_radius)
	# Whoever it reaches can read and copy the board right away; unsharing it later does not take that back.
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(board_size)
	async def share_board(board_id: int, user_id: str, can_edit: bool = True, can_share: bool = False, can_manage: bool = False):
		"""
		Share a Deck board with a user account
		:param board_id: the id of the board (obtainable with list_boards)
		:param user_id: the user id of the account to share the board with
		:param can_edit: optional - whether the user may edit the board's cards and stacks (default true)
		:param can_share: optional - whether the user may share the board with others (default false)
		:param can_manage: optional - whether the user may manage the board's settings, e.g. its title and labels (default false)
		:return: the created share
		"""
		# Sharing again is not a way to change the permissions of somebody who already has the board.
		if await find_user_acl(board_id, user_id) is not None:
			raise ValueError(f'Board {board_id} is already shared with {user_id!r}')
		return json.dumps(await deck_request('POST', f'/boards/{board_id}/acl', {
			'type': 0,
			'participant': user_id,
			'permissionEdit': can_edit,
			'permissionShare': can_share,
			'permissionManage': can_manage,
		}))

	@tool
	@impulse(board_radius)
	@destructive
	# The share can be made again just like it was.
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(board_size)
	async def unshare_board(board_id: int, user_id: str):
		"""
		Stop sharing a Deck board with a user account
		:param board_id: the id of the board (obtainable with list_boards)
		:param user_id: the user id of the account to unshare the board from (obtainable with list_boards - shares are listed in the board's acl)
		:return: the removed share
		"""
		acl = await find_user_acl(board_id, user_id)
		if acl is None:
			raise ValueError(f'Board {board_id} is not shared with {user_id!r}')
		return json.dumps(await deck_request('DELETE', f'/boards/{board_id}/acl/{acl["id"]}'))

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_board_cards(board_id: int, stack_id: Optional[int] = None, archived: bool = False):
		"""
		List all cards in a Deck board with their metadata.
		Each card includes its id and stack_id (needed for the other card tools, e.g. update_card, move_card, add_card_comment),
		title, description, stack, labels, assignees, due date, archived status, and done status.
		Use this tool to find cards when the agent only knows a card by name or context, not by id.
		Archived cards are only listed when archived is true.
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: optional - filter to cards in this specific stack only (obtainable with list_boards)
		:param archived: optional - true to list the archived cards instead of the active ones (default false)
		:return: list of cards with id, title, description, stack_id, stack_title, labels, assignees, due_date, archived, done, comments_count
		"""
		stacks = await deck_request('GET', f"/boards/{board_id}/stacks/archived" if archived else f"/boards/{board_id}/stacks")

		cards = []
		for stack in stacks:
			if stack_id is not None and stack['id'] != stack_id:
				continue
			for card in stack.get('cards', []):
				labels = []
				for label in card.get('labels', []):
					labels.append({
						'id': label['id'],
						'title': label['title'],
						'color': label['color'],
					})
				assignees = []
				for assignment in card.get('assignedUsers', []):
					participant = assignment.get('participant', {})
					assignees.append({
						'uid': participant.get('uid'),
						'displayname': participant.get('displayname'),
					})
				cards.append({
					'id': card['id'],
					'title': card['title'],
					'description': card.get('description', ''),
					'stack_id': stack['id'],
					'stack_title': stack['title'],
					'labels': labels,
					'assignees': assignees,
					'due_date': card.get('duedate'),
					'archived': card.get('archived', False),
					'done': card.get('done'),
					'comments_count': card.get('commentsCount', 0),
				})
		return json.dumps(cards)

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def add_card(board_id: int, stack_id: int, title: str, description: Optional[str] = None, due_date: Optional[str] = None):
		"""
		Create a new card in a list of a kanban board in the Nextcloud Deck app.
		When using this tool, you need to specify in which board and stack the card should be created.
		:param board_id: the id of the board the card should be created in, obtainable with list_boards
		:param stack_id: the id of the stack the card should be created in, obtainable with list_boards
		:param title: The title of the card
		:param description: Optional description for the card
		:param due_date: Optional due date in ISO format (YYYY-MM-DD)
		:return: the created card with its id
		"""
		description_with_ai_note = f"{description or ''}\n\nCreated by Nextcloud AI Assistant."

		payload = {
			'title': title,
			'description': description_with_ai_note,
			'type': 'plain',
			'order': 999,
		}
		if due_date:
			payload['duedate'] = due_date

		return json.dumps(await deck_request('POST', f'/boards/{board_id}/stacks/{stack_id}/cards', payload))

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def add_card_label(board_id: int, stack_id: int, card_id: int, label_id: int):
		"""
		Add a label to a card
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param label_id: the id of the label to add (obtainable with list_boards - labels are listed in board details)
		:return: success confirmation
		"""
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignLabel', {
			'labelId': label_id,
		}))

	@tool
	@impulse(assignment_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def assign_card_to_user(board_id: int, stack_id: int, card_id: int, user_id: str):
		"""
		Assign a card to a user
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param user_id: the user id to assign the card to
		:return: success confirmation
		"""
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignUser', {
			'userId': user_id,
		}))

	@tool
	@impulse(board_radius)
	@destructive
	# Deck keeps deleted cards around for undoing the deletion.
	@reversibility(Reversibility.RECOVERABLE)
	@item_count(1)
	async def delete_card(board_id: int, stack_id: int, card_id: int):
		"""
		Delete a card from a board
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card to delete (obtainable with list_board_cards)
		:return: success confirmation
		"""
		return json.dumps(await deck_request('DELETE', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}'))

	@tool
	@impulse(board_radius)
	@destructive_if(card_update_overwrites)
	@reversibility(card_update_reversibility)
	@item_count(1)
	async def update_card(board_id: int, stack_id: int, card_id: int, title: Optional[str] = None, description: Optional[str] = None, due_date: Optional[str] = None, done: Optional[bool] = None):
		"""
		Update the title, description, due date or done status of a card. Fields that are left out stay as they are.
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack the card is currently in (obtainable with list_board_cards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param title: optional new title of the card
		:param description: optional new description of the card, replacing the current one
		:param due_date: optional new due date in ISO format (YYYY-MM-DD or a full ISO datetime); pass an empty string to remove the due date
		:param done: optional - true to mark the card as done, false to mark it as not done
		:return: the updated card
		"""
		path = f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}'
		card = await deck_request('GET', path)
		# Deck reads the card by its id alone and then files it under the stack in the
		# path, so a stack_id that is off would move the card there, even onto another board.
		if card.get('stackId') != stack_id:
			raise ValueError(f'Card {card_id} is not in stack {stack_id} but in stack {card.get("stackId")}')
		# Deck restores a deleted card on any update.
		if card.get('deletedAt'):
			raise ValueError(f'Card {card_id} has been deleted')
		owner = card.get('owner')
		if isinstance(owner, dict):
			owner = owner.get('uid')

		current_done = card.get('done')
		if done is None:
			new_done = current_done
		elif done:
			# Keep the moment it was first marked done rather than moving it to now.
			new_done = current_done or datetime.now(timezone.utc).isoformat()
		else:
			new_done = None

		payload = {
			'title': title if title is not None else card['title'],
			'type': card.get('type') or 'plain',
			'owner': owner,
			'description': description if description is not None else (card.get('description') or ''),
			'order': card.get('order') or 0,
			'duedate': (due_date or None) if due_date is not None else card.get('duedate'),
			# Deck clears the done status of a card whenever an update leaves it out.
			'done': new_done,
		}
		return json.dumps(await deck_request('PUT', path, payload))

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def move_card(board_id: int, stack_id: int, card_id: int, target_stack_id: int):
		"""
		Move a card to another stack of the same board. The card is placed at the bottom of the target stack.
		Archived cards cannot be moved; unarchive them first.
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack the card is currently in (obtainable with list_board_cards)
		:param card_id: the id of the card to move (obtainable with list_board_cards)
		:param target_stack_id: the id of the stack to move the card to, on the same board (obtainable with list_boards)
		:return: the cards of the target stack in their new order
		"""
		# Deck would move the card onto another board's stack just the same, past the
		# board this call was classified against and without carrying its labels over.
		if not any(stack.get('id') == target_stack_id for stack in await deck_request('GET', f'/boards/{board_id}/stacks')):
			raise ValueError(f'Board {board_id} has no stack with id {target_stack_id}')
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/reorder', {
			'stackId': target_stack_id,
			'order': 999,
		}))

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def archive_card(board_id: int, stack_id: int, card_id: int):
		"""
		Archive a card. Archived cards are hidden from the board but can be unarchived again.
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_board_cards)
		:param card_id: the id of the card to archive (obtainable with list_board_cards)
		:return: the archived card
		"""
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/archive'))

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def unarchive_card(board_id: int, stack_id: int, card_id: int):
		"""
		Unarchive a previously archived card, making it visible on the board again.
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_board_cards)
		:param card_id: the id of the card to unarchive (obtainable with list_board_cards)
		:return: the unarchived card
		"""
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/unarchive'))

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def remove_card_label(board_id: int, stack_id: int, card_id: int, label_id: int):
		"""
		Remove a label from a card
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_board_cards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param label_id: the id of the label to remove (obtainable with list_board_cards)
		:return: success confirmation
		"""
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/removeLabel', {
			'labelId': label_id,
		}))

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def unassign_user_from_card(board_id: int, stack_id: int, card_id: int, user_id: str):
		"""
		Remove a user's assignment from a card
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_board_cards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param user_id: the user id of the assignee to remove (obtainable with list_board_cards)
		:return: success confirmation
		"""
		return json.dumps(await deck_request('PUT', f'/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/unassignUser', {
			'userId': user_id,
		}))

	# --- Stacks ---

	@tool
	@impulse(board_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def create_stack(board_id: int, title: str):
		"""
		Create a new stack (list/column) on a board. The stack is added after the existing stacks.
		:param board_id: the id of the board (obtainable with list_boards)
		:param title: the title of the stack
		:return: the created stack with its id
		"""
		stacks = await deck_request('GET', f'/boards/{board_id}/stacks')
		order = max((stack.get('order') or 0 for stack in stacks), default=-1) + 1
		return json.dumps(await deck_request('POST', f'/boards/{board_id}/stacks', {
			'title': title,
			'order': order,
		}))

	@tool
	@impulse(board_radius)
	# The title it replaces is not kept anywhere.
	@destructive
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def rename_stack(board_id: int, stack_id: int, title: str):
		"""
		Rename a stack (list/column) on a board
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param title: the new title of the stack
		:return: the updated stack
		"""
		path = f'/boards/{board_id}/stacks/{stack_id}'
		stack = await deck_request('GET', path)
		# Deck reads the stack by its id alone and then files it under the board in the
		# path, so a board_id that is off would move the whole stack onto that board.
		if stack.get('boardId') != board_id:
			raise ValueError(f'Stack {stack_id} is not on board {board_id} but on board {stack.get("boardId")}')
		# Deck restores a deleted stack on any update.
		if stack.get('deletedAt'):
			raise ValueError(f'Stack {stack_id} has been deleted')
		return json.dumps(await deck_request('PUT', path, {
			'title': title,
			'order': stack.get('order') or 0,
		}))

	@tool
	@impulse(board_radius)
	@destructive
	# Deck keeps deleted stacks around for undoing the deletion.
	@reversibility(Reversibility.RECOVERABLE)
	@item_count(stack_size)
	async def delete_stack(board_id: int, stack_id: int):
		"""
		Delete a stack (list/column) from a board, together with all cards on it
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack to delete (obtainable with list_boards)
		:return: the deleted stack
		"""
		return json.dumps(await deck_request('DELETE', f'/boards/{board_id}/stacks/{stack_id}'))

	# --- Card Comments (OCS API) ---

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_card_comments(card_id: int, limit: int = 20, offset: int = 0):
		"""
		List all comments on a Deck card
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param limit: maximum number of comments to return (default 20)
		:param offset: pagination offset (default 0)
		:return: list of comments with id, message, author, and creation date
		"""
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments', params={
			'limit': limit,
			'offset': offset,
		}))

	@tool
	@impulse(card_radius)
	# Whoever sees the board may have read it.
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def add_card_comment(card_id: int, message: str, parent_id: Optional[int] = None):
		"""
		Add a comment to a Deck card
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param message: the comment text (max 965 characters)
		:param parent_id: optional id of a parent comment for threaded replies (obtainable with list_card_comments)
		:return: the created comment
		"""
		message_with_ai_note = f"{message}\n\nPosted by Nextcloud AI Assistant."
		check_comment_length(message_with_ai_note)
		payload = {'message': message_with_ai_note}
		if parent_id is not None:
			payload['parentId'] = parent_id
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments', json=payload))

	@tool
	@impulse(card_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def update_card_comment(card_id: int, comment_id: int, message: str):
		"""
		Update an existing comment on a Deck card. Only the comment author can update their own comments.
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param comment_id: the id of the comment to update (obtainable with list_card_comments)
		:param message: the new comment text (max 965 characters)
		:return: the updated comment
		"""
		message_with_ai_note = f"{message}\n\nEdited by Nextcloud AI Assistant."
		check_comment_length(message_with_ai_note)
		return json.dumps(await nc.ocs('PUT', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments/{comment_id}', json={
			'message': message_with_ai_note,
		}))

	@tool
	@impulse(card_radius)
	@destructive
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def delete_card_comment(card_id: int, comment_id: int):
		"""
		Delete a comment from a Deck card. Only the comment author can delete their own comments.
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param comment_id: the id of the comment to delete (obtainable with list_card_comments)
		:return: confirmation of deletion
		"""
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments/{comment_id}'))

	return [
		list_boards,
		create_board,
		share_board,
		unshare_board,
		list_board_cards,
		add_card,
		add_card_label,
		assign_card_to_user,
		delete_card,
		update_card,
		move_card,
		archive_card,
		unarchive_card,
		remove_card_label,
		unassign_user_from_card,
		create_stack,
		rename_stack,
		delete_stack,
		list_card_comments,
		add_card_comment,
		update_card_comment,
		delete_card_comment,
	]

def get_category_name():
	return "Deck"

async def is_available(nc: AsyncNextcloudApp):
	return 'deck' in await nc.capabilities
