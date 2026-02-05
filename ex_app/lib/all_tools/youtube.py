# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from nc_py_api import AsyncNextcloudApp
from langchain_community.tools import YouTubeSearchTool


async def get_tools(nc: AsyncNextcloudApp):

	yt_search = YouTubeSearchTool()
	return [
		yt_search,
	]

def get_category_name():
	return "YouTube"

async def is_available(nc: AsyncNextcloudApp):
	return True
