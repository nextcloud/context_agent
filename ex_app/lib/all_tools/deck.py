# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import LogLvl

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def list_boards():
		"""
		List all existing kanban boards available in the Nextcloud Deck app to the current user with their available info
		:return: a dictionary with all decks of the user
		"""

		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards?details=true", headers={
			"Content-Type": "application/json",
		})

		return response.text

	

	@tool
	@dangerous_tool
	async def add_card(board_id: int, stack_id: int, title: str):
		"""
		Create a new card in a list of a kanban board in the Nextcloud Deck app.
		When using this tool, you need to specify in which board and map the card should be created.
		:param board_id: the id of the board the card should be created in, obtainable with list_boards
		:param stack_id: the id of the stack the card should be created in, obtainable with list_boards
		:param title: The title of the card
		:return: bool
		"""

		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards", headers={
			"Content-Type": "application/json",
		}, json={
					'title': title,
					'description': 'Created by Nextcloud AI Assistant.',
					'type': 'plain',
					'order': 999,
				})


		return True

	return [
		list_boards,
		add_card
	]

def get_category_name():
	return "Deck"

async def is_available(nc: AsyncNextcloudApp):
	return 'deck' in await nc.capabilities