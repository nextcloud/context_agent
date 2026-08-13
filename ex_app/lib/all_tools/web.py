# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import niquests
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool
from ex_app.lib.all_tools.lib.files import (
	MAX_FILE_SIZE,
	TEXT_LIKE_MIMETYPE_PARTS,
	get_file_content_from_int_link,
	is_an_internal_file_link,
)


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def web_fetch(url: str) -> str:
		"""
		Fetch the contents of an external web page via HTTP.
		Use this for any URL on the public internet or intranet (e.g., https://www.eff.org/).
		:param url: the HTTP(S) URL of the web page to fetch
		:return: the raw web page content (HTML, JSON, etc.)
		"""
		# Detect Nextcloud-internal file links and fetch via the internal API, for the models that would still call this tool.
		if is_an_internal_file_link(nc, url):
			return await get_file_content_from_int_link(nc, url)

		# Pre-flight HEAD request to check content type and size before downloading
		head_res = await niquests.async_api.head(url, allow_redirects=True)

		content_type = head_res.headers.get('Content-Type', '')
		if not any(t in content_type for t in TEXT_LIKE_MIMETYPE_PARTS):
			return f"(binary or unknown content detected: {content_type or 'no Content-Type header'}. Cannot display as text.)"

		# download first 100kB of the file
		res = await niquests.async_api.get(url, headers={'Range': f'bytes=0-{MAX_FILE_SIZE - 1}'})
		text = res.text or "(empty content)"

		if res.status_code == 206:
			text += "\n\n(truncated: only the first 100 kB of content was fetched.)"

		return text

	return [
		web_fetch,
	]

def get_category_name():
	return "Web access"

async def is_available(nc: AsyncNextcloudApp):
	return True