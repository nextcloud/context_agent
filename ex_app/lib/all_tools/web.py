# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import niquests
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def web_fetch(url: str) -> str:
		"""
		Get the contents of a web page via HTTP
		:param url: The HTTP URL to the web page (e.g. https://nextcloud.com/team/ )
		:return: the web page content
		"""
		res = await niquests.get(url)
		return res.text()

	return [
		web_fetch,
	]

def get_category_name():
	return "Web access"

async def is_available(nc: AsyncNextcloudApp):
	return True