# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse


async def get_tools(nc: AsyncNextcloudApp):

	web_search = DuckDuckGoSearchResults(output_format="list")

	# Wrapped rather than returned as-is: the impulse radius lives on the wrapped
	# function, and a LangChain tool class has none to carry it. Without the hook
	# the call would fall back to the widest radius and be confirmed every time.
	@tool
	@impulse(ImpulseRadius.SELF)
	async def duckduckgo_results_json(query: str) -> list[dict]:
		"""
		A wrapper around Duck Duck Go Search. Useful for when you need to answer questions about current events.
		:param query: search query to look up
		:return: a list of search results, each with a title, a link and a snippet
		"""
		return await web_search.ainvoke({"query": query})

	return [
		duckduckgo_results_json,
	]

def get_category_name():
	return "DuckDuckGo"

async def is_available(nc: AsyncNextcloudApp):
	return True
