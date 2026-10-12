# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime

import pytz
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse
from ex_app.lib.all_tools.lib.tz import user_timezone_async


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@impulse(ImpulseRadius.SELF)
	async def get_current_datetime():
		"""
		Get the current date and time in the user's timezone
		:return: the current date and time, e.g. '2026-05-11 15:30 (Europe/Berlin)'.
			Falls back to UTC if the user's timezone is unknown
		"""
		tz_name = await user_timezone_async(nc)
		try:
			tz = pytz.timezone(tz_name) if tz_name else pytz.UTC
		except pytz.UnknownTimeZoneError:
			tz = pytz.UTC
		return f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M')} ({tz.zone})"

	return [
		get_current_datetime,
	]

def get_category_name():
	return "Date and time"

async def is_available(nc: AsyncNextcloudApp):
	return True
