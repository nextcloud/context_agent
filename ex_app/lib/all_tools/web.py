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
		Fetch the contents of an external web page via HTTP.
		This is NOT for Nextcloud-internal file links (like https://<host>/f/12345), use get_file_content_by_file_link for those.
		Use this for any URL on the public internet or intranet (e.g., https://www.eff.org/).
		:param url: the HTTP(S) URL of the web page to fetch
		:return: the raw web page content (HTML, JSON, etc.)
		"""
		res = await niquests.async_api.get(url)
		return res.text or "(empty content)"

	return [
		web_fetch,
	]

def get_category_name():
	return "Web access"

async def is_available(nc: AsyncNextcloudApp):
	return True