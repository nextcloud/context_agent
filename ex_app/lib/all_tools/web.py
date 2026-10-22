# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from urllib.parse import urljoin, urlsplit
from urllib.request import getproxies

import niquests
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse
from ex_app.lib.all_tools.lib.files import (
	MAX_FILE_SIZE,
	TEXT_LIKE_MIMETYPE_PARTS,
	get_file_content_from_int_link,
	is_an_internal_file_link,
	is_known_nc_host,
)
from ex_app.lib.all_tools.lib.url_guard import (
	BlockedUrlError,
	PublicAddressResolver,
	assert_public_url,
)

MAX_REDIRECTS = 5


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@impulse(ImpulseRadius.SELF)
	async def web_fetch(url: str) -> str:
		"""
		Fetch the contents of an external web page via HTTP.
		Use this for any URL on the public internet or intranet (e.g., https://www.eff.org/).
		:param url: the HTTP(S) URL of the web page to fetch
		:return: the raw web page content (HTML, JSON, etc.)
		"""
		# Detect Nextcloud-internal file links and fetch via the internal API, for the models that would still call this tool.
		if await is_an_internal_file_link(nc, url):
			return await get_file_content_from_int_link(nc, url)

		# The resolver checks the addresses again when the client connects. Thus a DNS
		# server cannot send a public address to validate_hop and then an internal
		# address to the client (DNS rebinding). Proxies from the environment can have
		# an internal address, and the proxy and not this resolver then resolves the target.
		resolver = PublicAddressResolver(
			urlsplit(proxy).hostname or '' for scheme, proxy in getproxies().items() if scheme != 'no'
		)

		async def validate_hop(hop_url: str) -> str | None:
			"""Apply the SSRF gate to one URL.

			Return an error message if the URL is not permitted. Return None if it is
			permitted.
			"""
			# Permit the URLs of this Nextcloud instance. The tool does the fetch without
			# app_api authentication. Thus these URLs are safe. The tool also applies this
			# function to each redirect.
			if await is_known_nc_host(nc, hop_url):
				resolver.allowed_hosts.add((urlsplit(hop_url).hostname or '').lower())
				return None
			# The URL must use the HTTP or the HTTPS scheme. All the addresses of the host
			# must be public. This blocks localhost, the private ranges, the link-local
			# range with the metadata address 169.254.169.254, and the names that end
			# with `.local`.
			try:
				await assert_public_url(hop_url)
			except BlockedUrlError as e:
				return str(e)
			return None

		error = await validate_hop(url)
		if error:
			return error

		async with niquests.AsyncSession(resolver=resolver) as session:
			try:
				return await _fetch(session, url, validate_hop)
			except niquests.RequestException as e:
				return f"(failed to fetch URL: {e})"

	return [
		web_fetch,
	]


async def _fetch(session: niquests.AsyncSession, url: str, validate_hop) -> str:
	# The Range header requests only the first 100 kB. But this header is only a
	# request. Some servers ignore it and send the full body with a 200 status.
	# Thus read the response as a stream and stop at the limit. The tool keeps a
	# maximum of 100 kB in memory for each response of any size.
	# The tool does not follow redirects automatically. It applies the SSRF check to
	# each redirect. Thus a public URL cannot redirect the tool to an internal target.
	current_url = url
	res = None
	for _ in range(MAX_REDIRECTS + 1):
		res = await session.get(
			current_url,
			headers={'Range': f'bytes=0-{MAX_FILE_SIZE - 1}'},
			allow_redirects=False,
			timeout=30,
			stream=True,
		)
		if res.status_code and 300 <= res.status_code < 400 and 'Location' in res.headers:
			# If the redirect target is a relative URL, make it absolute. Use the
			# current URL as the base.
			next_url = urljoin(current_url, res.headers['Location'])
			await res.close()
			error = await validate_hop(next_url)
			if error:
				return error
			current_url = next_url
			continue
		break
	else:
		if res is not None:
			await res.close()
		return "(refused: too many redirects)"

	failed_req = False
	content = b""
	truncated = False
	# With `stream=True`, the connection stays open until the body is fully read or
	# the response is closed. The tool stops the read before the end of the body.
	# Thus always close the response in a `finally` block.
	try:
		content_type = res.headers.get('Content-Type', '')
		if not any(t in content_type for t in TEXT_LIKE_MIMETYPE_PARTS):
			return f"(binary or unknown content detected: {content_type or 'no Content-Type header'}. Cannot display as text.)"

		if res.status_code is None or res.status_code >= 400:
			failed_req = True

		# Read a maximum of MAX_FILE_SIZE bytes.
		async for chunk in await res.iter_content(chunk_size=8192):
			content += chunk
			if len(content) > MAX_FILE_SIZE:
				content = content[:MAX_FILE_SIZE]
				truncated = True
				break

		# A server that honors the Range header sends at most MAX_FILE_SIZE bytes with
		# a 206 status. The total size is in the Content-Range header.
		if res.status_code == 206 and _is_partial(res.headers.get('Content-Range', ''), len(content)):
			truncated = True
	finally:
		await res.close()

	body = content.decode(res.encoding or 'utf-8', errors='replace') if content else ""

	if failed_req:
		text = f"(HTTP {res.status_code} error fetching URL)"
		if body:
			text += f"\n\nServer response:\n{body}"
	else:
		text = body or "(empty content)"

	if truncated:
		text += "\n\n(truncated: only the first 100 kB of content was fetched.)"

	return text


def _is_partial(content_range: str, received: int) -> bool:
	"""Check whether a `Content-Range: bytes <start>-<end>/<total>` header shows more data than *received*."""
	total = content_range.rpartition('/')[2].strip()
	return not total.isdigit() or int(total) > received


def get_category_name():
	return "Web access"

async def is_available(nc: AsyncNextcloudApp):
	return True