# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from nc_py_api import AsyncNextcloudApp
from langchain_community.tools import DuckDuckGoSearchResults


async def get_tools(nc: AsyncNextcloudApp):

	web_search = DuckDuckGoSearchResults(output_format="list")
	return [
		web_search,
	]

def get_category_name():
	return "DuckDuckGo"

async def is_available(nc: AsyncNextcloudApp):
	return True
