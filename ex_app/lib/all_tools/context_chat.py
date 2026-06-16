# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Literal
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.task_processing import run_task
from ex_app.lib.all_tools.lib.decorator import safe_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def ask_context_chat(
		question: str,
		scope_type: Literal['none', 'source', 'provider'] = 'none',
		scope_list: list[str] | None = None,
	) -> str:
		"""
		Ask the context chat oracle a question about the user's documents. It knows the contents of all of the users documents.
		This is often easier than searching for documents and then fetching their contents when trying to answer questions.
		:param question: The question to ask
		:param scope_type: Optional. Restricts which documents the oracle searches.
			- "none" (default): search across all of the user's documents.
			- "source": restrict the search to specific items listed in scope_list.
			- "provider": restrict the search to specific content providers listed in scope_list.
		:param scope_list: Required when scope_type is "source" or "provider"; ignored otherwise.
			- For scope_type "source": a list of source IDs in the format "<appId>__<providerId>: <itemId>"
			  (e.g., "files__default: 12345" for the file with Nextcloud file id 12345). File ids can be
			  obtained from the file-related tools or unified search.
			- For scope_type "provider": a list of provider keys in the format "<appId>__<providerId>"
			  (e.g., "files__default" for files, "mail__messages" for mail).
		:return: the answer from context chat
		"""

		task_input = {
			'prompt': question,
			'scopeType': scope_type,
			'scopeList': scope_list or [],
			'scopeListMeta': '',
		}
		task_output = (await run_task(nc,  "context_chat:context_chat", task_input)).output
		return task_output['output']

	return [
		ask_context_chat,
	]

def get_category_name():
	return "Context chat"

async def is_available(nc: AsyncNextcloudApp):
	tasktypes = (await nc.ocs('GET', '/ocs/v2.php/taskprocessing/tasktypes'))['types'].keys()
	return 'context_chat:context_chat' in tasktypes