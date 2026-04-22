# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def list_boards():
		"""
		List all existing kanban boards available in the Nextcloud Deck app for the current user with their available info
		:return: a dictionary with all decks of the user
		"""

		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards?details=true", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})

		return json.dumps(response.json())

	@tool
	@safe_tool
	async def list_board_cards(board_id: int, stack_id: Optional[int] = None):
		"""
		List all cards in a Deck board with their metadata.
		Each card includes its id (needed for add_card_comment, add_card_label, assign_card_to_user, delete_card),
		title, description, stack, labels, assignees, due date, archived status, and done status.
		Use this tool to find cards when the agent only knows a card by name or context, not by id.
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: optional - filter to cards in this specific stack only (obtainable with list_boards)
		:return: list of cards with id, title, description, stack_id, stack_title, labels, assignees, due_date, archived, done, comments_count
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})
		stacks = response.json()

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
	@dangerous_tool
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

		response = await nc._session._create_adapter().request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json=payload)

		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def add_card_label(board_id: int, stack_id: int, card_id: int, label_id: int):
		"""
		Add a label to a card
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param label_id: the id of the label to add (obtainable with list_boards - labels are listed in board details)
		:return: success confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignLabel", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json={
			'labelId': label_id
		})

		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def assign_card_to_user(board_id: int, stack_id: int, card_id: int, user_id: str):
		"""
		Assign a card to a user
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param user_id: the user id to assign the card to
		:return: success confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignUser", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json={
			'userId': user_id
		})

		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def delete_card(board_id: int, stack_id: int, card_id: int):
		"""
		Delete a card from a board
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card to delete (obtainable with list_board_cards)
		:return: success confirmation
		"""
		response = await nc._session._create_adapter().request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})

		return json.dumps(response.json())

	# --- Card Comments (OCS API) ---

	@tool
	@safe_tool
	async def list_card_comments(card_id: int, limit: int = 20, offset: int = 0):
		"""
		List all comments on a Deck card
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param limit: maximum number of comments to return (default 20)
		:param offset: pagination offset (default 0)
		:return: list of comments with id, message, author, and creation date
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments', params={
			'limit': limit,
			'offset': offset,
		})

	@tool
	@dangerous_tool
	async def add_card_comment(card_id: int, message: str, parent_id: Optional[int] = None):
		"""
		Add a comment to a Deck card
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param message: the comment text (max 1000 characters)
		:param parent_id: optional id of a parent comment for threaded replies (obtainable with list_card_comments)
		:return: the created comment
		"""
		payload = {'message': message}
		if parent_id is not None:
			payload['parentId'] = parent_id
		return await nc.ocs('POST', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments', json=payload)

	@tool
	@dangerous_tool
	async def update_card_comment(card_id: int, comment_id: int, message: str):
		"""
		Update an existing comment on a Deck card. Only the comment author can update their own comments.
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param comment_id: the id of the comment to update (obtainable with list_card_comments)
		:param message: the new comment text (max 1000 characters)
		:return: the updated comment
		"""
		return await nc.ocs('PUT', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments/{comment_id}', json={
			'message': message,
		})

	@tool
	@dangerous_tool
	async def delete_card_comment(card_id: int, comment_id: int):
		"""
		Delete a comment from a Deck card. Only the comment author can delete their own comments.
		:param card_id: the id of the card (obtainable with list_board_cards)
		:param comment_id: the id of the comment to delete (obtainable with list_card_comments)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments/{comment_id}')

	return [
		list_boards,
		list_board_cards,
		add_card,
		add_card_label,
		assign_card_to_user,
		delete_card,
		list_card_comments,
		add_card_comment,
		update_card_comment,
		delete_card_comment,
	]

def get_category_name():
	return "Deck"

async def is_available(nc: AsyncNextcloudApp):
	return 'deck' in await nc.capabilities
