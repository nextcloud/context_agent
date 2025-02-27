from datetime import datetime, timezone, timedelta
from time import sleep
from typing import Optional

import httpx
import pytz
from langchain_core.tools import tool
from nc_py_api import Nextcloud
from nc_py_api.ex_app import LogLvl
import xml.etree.ElementTree as ET
import vobject

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool
from ex_app.lib.logger import log


def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def list_decks():
		"""
		List all existing kanban decks with their available info
		:return: a dictionary with all decks of the user
		"""

		response = nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0/boards?details=true", headers={
			"Content-Type": "application/json",
		})

		return response.text

	

	@tool
	@dangerous_tool
	def add_card(deck_id: int, stack_id: int, title: str):
		"""
		Create a new card in a list of a kanban deck. 
		When using this tool, you need to specify in which deck and map the card should be created.
		:param deck_id: the id of the deck the card should be created in, obtainable with list_decks
		:param stack_id: the id of the stack the card should be created in, obtainable with list_decks
		:param title: The title of the card
		:return: bool
		"""

		response = nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/deck/api/v1.0//boards/{deck_id}/stacks/{stack_id}/cards", headers={
			"Content-Type": "application/json",
		}, json={
					'title': title,
					'type': 'plain',
					'order': 999,
				})


		return True

	return [
		list_decks,
		add_card
	]