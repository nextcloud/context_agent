# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import inspect
import logging
import traceback

from nc_py_api import NextcloudException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger('context_agent')
logger.setLevel(logging.INFO)

async def log(nc, level, content):
	logger.log((level+1)*10, content)
	try:
		await nc.log(level, content)
	except asyncio.CancelledError:
		raise
	except Exception:
		pass

MAX_RESPONSE_TEXT_LENGTH = 2000


async def _ocs_response_text(e: NextcloudException) -> str | None:
	response = getattr(e, 'response', None)
	if response is None:
		return None
	try:
		text = response.text
		# niquests' AsyncResponse.text is a coroutine, the sync Response's is a str
		if inspect.isawaitable(text):
			text = await text
	except asyncio.CancelledError:
		raise
	except Exception:
		logger.debug("Failed to read OCS error response body", exc_info=True)
		return None
	if not isinstance(text, str):
		return None
	text = text.strip()
	if not text:
		return None
	if len(text) > MAX_RESPONSE_TEXT_LENGTH:
		text = text[:MAX_RESPONSE_TEXT_LENGTH] + '... (truncated)'
	return text


async def format_exception_with_ocs_body(e: BaseException) -> str:
	"""Format an exception with its traceback, like traceback.format_exception,
	and append the body of any OCS error response in its chain, since
	str(NextcloudException) only carries the status code and reason."""
	formatted = ''.join(traceback.format_exception(e))
	seen = set()
	current: BaseException | None = e
	while current is not None and id(current) not in seen:
		seen.add(id(current))
		if isinstance(current, NextcloudException):
			text = await _ocs_response_text(current)
			if text is not None:
				formatted += f'OCS response for [{current.status_code}]: {text}\n'
		current = current.__cause__ or current.__context__
	return formatted
