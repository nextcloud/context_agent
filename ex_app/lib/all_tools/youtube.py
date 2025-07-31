# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import Nextcloud
from langchain_community.tools import YouTubeSearchTool

from ex_app.lib.all_tools.lib.decorator import safe_tool


async def get_tools(nc: Nextcloud):

	yt_search = YouTubeSearchTool()
	return [
		yt_search,
	]

def get_category_name():
	return "YouTube"

def is_available(nc: Nextcloud):
	return True
