# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import logging
from typing import Literal

import pydantic
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import timed_memoize
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse, read_only
from ex_app.lib.all_tools.lib.task_processing import run_task

LOGGER = logging.getLogger(__name__)
CC_PROVIDERS_TTL = 60 * 60  # 1h
CONTEXT_CHAT_TASK_TYPE = "context_chat:context_chat"
CONTEXT_CHAT_SEARCH_TASK_TYPE = "context_chat:context_chat_search"


class CcProvider(pydantic.BaseModel):
	id: str
	label: str
	icon: str


@timed_memoize(CC_PROVIDERS_TTL)
async def _fetch_context_chat_providers(nc: AsyncNextcloudApp) -> list[CcProvider]:
	response = await nc._session._create_adapter(False).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/context_chat/providers", headers={
		"Content-Type": "application/json",
		"OCS-APIREQUEST": "true",
	})

	try:
		return pydantic.TypeAdapter(list[CcProvider]).validate_python(response.json())
	except Exception as e:
		LOGGER.warning(f"Validation of context_chat providers response failed: {e}", exc_info=e)
		return []


async def get_context_chat_providers(nc: AsyncNextcloudApp, pretty: bool = False) -> list[CcProvider] | list[str]:
	result = await _fetch_context_chat_providers(nc)

	if not pretty:
		return result

	return [f"{p.label}({p.id})" for p in result]


async def _check_provider_scope_valid(nc: AsyncNextcloudApp, scope_list: list[str]):
	available_providers = await get_context_chat_providers(nc)
	provider_keys = {p.id for p in available_providers}
	for scope in scope_list:
		if scope not in provider_keys:
			raise RuntimeError(f"Provider {scope} is not an available provider. Available providers: {provider_keys}")


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_context_chat_providers() -> list[str]:
		"""
		Refreshes the content providers list available to context chat (e.g., files, mail).
		:return: list of available providers
		"""
		await _fetch_context_chat_providers.cache_invalidate(nc)
		return await get_context_chat_providers(nc, True)

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
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
			- "none" (default): search across all of the user's documents and content providers.
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

		if scope_type != "none" and not scope_list:
			raise RuntimeError("A set scope requires at least one item in the scope list")

		if scope_type == "provider":
			await _check_provider_scope_valid(nc, scope_list)

		task_input = {
			"prompt": question,
			"scopeType": scope_type,
			"scopeList": scope_list or [],
			"scopeListMeta": "",
		}
		task_output = (await run_task(nc, CONTEXT_CHAT_TASK_TYPE, task_input)).output
		return task_output["output"]

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def search_context_chat(
		prompt: str,
		scope_type: Literal['none', 'source', 'provider'] = 'none',
		scope_list: list[str] | None = None,
		limit: int = 30,
	) -> str:
		"""
		Search the context chat database for the user's documents. It uses semantic search to find similar documents.
		This is for only searching the documents without an LLM explanation.
		:param prompt: The text to find similar documents for
		:param scope_type: Optional. Restricts which documents the oracle searches.
			- "none" (default): search across all of the user's documents and content providers.
			- "source": restrict the search to specific items listed in scope_list.
			- "provider": restrict the search to specific content providers listed in scope_list.
		:param scope_list: Required when scope_type is "source" or "provider"; ignored otherwise.
			- For scope_type "source": a list of source IDs in the format "<appId>__<providerId>: <itemId>"
			  (e.g., "files__default: 12345" for the file with Nextcloud file id 12345). File ids can be
			  obtained from the file-related tools or unified search.
			- For scope_type "provider": a list of provider keys in the format "<appId>__<providerId>"
			  (e.g., "files__default" for files, "mail__messages" for mail).
		:param limit: The max no. of documents to return, default 30, max 200.
		:return: the answer from context chat
		"""

		if scope_type != "none" and not scope_list:
			raise RuntimeError("A set scope requires at least one item in the scope list")

		if scope_type == "provider":
			await _check_provider_scope_valid(nc, scope_list)

		task_input = {
			"prompt": prompt,
			"scopeType": scope_type,
			"scopeList": scope_list or [],
			"scopeListMeta": "",
			"limit": min(200, max(1, limit)),
		}
		task_output = (await run_task(nc, CONTEXT_CHAT_SEARCH_TASK_TYPE, task_input)).output
		return task_output["sources"]

	return [
		list_context_chat_providers,
		ask_context_chat,
		search_context_chat,
	]

def get_category_name():
	return "Context chat"

async def is_available(nc: AsyncNextcloudApp):
	tasktypes = (await nc.ocs('GET', '/ocs/v2.php/taskprocessing/tasktypes'))['types'].keys()
	return CONTEXT_CHAT_TASK_TYPE in tasktypes