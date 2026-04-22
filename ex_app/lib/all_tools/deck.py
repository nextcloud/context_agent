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

	return [
		list_boards,
		list_board_cards,
		add_card,
		add_card_label,
		assign_card_to_user,
		delete_card
	]

def get_category_name():
	return "Deck"

async def is_available(nc: AsyncNextcloudApp):
	return 'deck' in await nc.capabilities
