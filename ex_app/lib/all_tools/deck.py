# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
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

		return response.json()

	

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

		return response.json()

	@tool
	@dangerous_tool
	async def update_card(card_id: int, title: Optional[str] = None, description: Optional[str] = None, due_date: Optional[str] = None):
		"""
		Update an existing card in a kanban board
		:param card_id: the id of the card to update (obtainable with list_boards)
		:param title: New title for the card
		:param description: New description for the card
		:param due_date: New due date in ISO format (YYYY-MM-DD)
		:return: the updated card
		"""
		payload = {}
		if title is not None:
			payload['title'] = title
		if description is not None:
			payload['description'] = description
		if due_date is not None:
			payload['duedate'] = due_date

		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/cards/{card_id}", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json=payload)

		return response.json()

	@tool
	@dangerous_tool
	async def move_card(board_id: int, stack_id: int, card_id: int, new_stack_id: int, order: int):
		"""
		Move a card to a different stack (column) on the board
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card to move (obtainable with list_boards)
		:param new_stack_id: the id of the destination stack (obtainable with list_boards)
		:param order: the position in the new stack (999 puts it at the end)
		:return: the moved card
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/reorder", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json={
			'stackId': new_stack_id,
			'order': order
		})

		return response.json()

	@tool
	@dangerous_tool
	async def add_card_label(board_id: int, stack_id: int, card_id: int, label_id: int):
		"""
		Add a label to a card
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_boards)
		:param label_id: the id of the label to add (obtainable with list_boards - labels are listed in board details)
		:return: success confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignLabel", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json={
			'labelId': label_id
		})

		return response.json()

	@tool
	@dangerous_tool
	async def assign_card_to_user(board_id: int, stack_id: int, card_id: int, user_id: str):
		"""
		Assign a card to a user
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_boards)
		:param user_id: the user id to assign the card to
		:return: success confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignUser", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json={
			'userId': user_id
		})

		return response.json()

	@tool
	@dangerous_tool
	async def add_card_comment(board_id: int, stack_id: int, card_id: int, comment: str):
		"""
		Add a comment to a card
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card (obtainable with list_boards)
		:param comment: the comment text to add
		:return: the created comment
		"""
		response = await nc._session._create_adapter().request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/comments", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		}, json={
			'message': comment
		})

		return response.json()

	@tool
	@dangerous_tool
	async def delete_card(board_id: int, stack_id: int, card_id: int):
		"""
		Delete a card from a board
		:param board_id: the id of the board (obtainable with list_boards)
		:param stack_id: the id of the stack (obtainable with list_boards)
		:param card_id: the id of the card to delete (obtainable with list_boards)
		:return: success confirmation
		"""
		response = await nc._session._create_adapter().request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}", headers={
			"Content-Type": "application/json",
			"OCS-APIREQUEST": "true",
		})

		return response.json()

	return [
		list_boards,
		add_card,
		update_card,
		move_card,
		add_card_label,
		assign_card_to_user,
		add_card_comment,
		delete_card
	]

def get_category_name():
	return "Deck"

async def is_available(nc: AsyncNextcloudApp):
	return 'deck' in await nc.capabilities