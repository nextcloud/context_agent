# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_community.tools import YouTubeSearchTool
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse


async def get_tools(nc: AsyncNextcloudApp):

	yt_search = YouTubeSearchTool()

	# Wrapped rather than returned as-is: the impulse radius lives on the wrapped
	# function, and a LangChain tool class has none to carry it. Without the hook
	# the call would fall back to the widest radius and be confirmed every time.
	@tool
	@impulse(ImpulseRadius.SELF)
	async def youtube_search(query: str) -> str:
		"""
		Search for youtube videos associated with a person.
		:param query: a comma separated list, the first part contains a person name and the second a number that is the maximum number of video results to return aka num_results. the second part is optional
		:return: the URLs of the videos found
		"""
		return await yt_search.ainvoke({"query": query})

	return [
		youtube_search,
	]

def get_category_name():
	return "YouTube"

async def is_available(nc: AsyncNextcloudApp):
	return True
