# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime

import pytz

from ex_app.lib.all_tools.lib.decorator import timed_memoize


def get_timezone(timezone: str | None) -> str:
	if timezone is None or not timezone.strip():
		raise ValueError("No timezone given and the user's profile has none set; provide a valid IANA timezone name, e.g. 'America/New_York'")
	try:
		return pytz.timezone(timezone).zone
	except pytz.UnknownTimeZoneError:
		raise ValueError(f"Invalid timezone '{timezone}'. Must be a valid IANA name, e.g. 'America/New_York'") from None


def user_timezone(nc) -> str | None:
	try:
		user = nc.ocs('GET', '/ocs/v2.php/cloud/user') or {}
		return user.get('timezone') or None
	except Exception:
		return None


@timed_memoize(300)
async def user_timezone_async(nc) -> str | None:
	try:
		user = (await nc.ocs('GET', '/ocs/v2.php/cloud/user')) or {}
		return user.get('timezone') or None
	except Exception:
		return None


def profile_timezone(timezone: str | None):
	"""The pytz timezone for a profile timezone name, or None when it is unset or unknown."""
	try:
		return pytz.timezone(timezone) if timezone else None
	except pytz.UnknownTimeZoneError:
		return None


def since_timestamp(name: str, value: str, timezone: str | None) -> int:
	"""Unix timestamp of the ISO datetime a tool got as parameter name, read in timezone when it has no UTC offset."""
	try:
		parsed = datetime.fromisoformat(value.strip())
	except ValueError:
		raise ValueError(f"{name} {value!r} is not an ISO datetime; use e.g. '2024-12-01T14:00:00+01:00'") from None
	if parsed.tzinfo is None:
		tz = profile_timezone(timezone)
		if tz is None:
			raise ValueError(
				f"{name} {value!r} has no UTC offset and the user's profile has no timezone; "
				"add the offset, e.g. '2024-12-01T14:00:00+01:00'"
			)
		parsed = tz.localize(parsed)
	return int(parsed.timestamp())


def starts_at_timestamp(starts_at: str, timezone: str | None) -> int:
	parsed = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
	if parsed.tzinfo is None or parsed.utcoffset() is None:
		# only naive values need a timezone to be interpreted
		parsed = pytz.timezone(get_timezone(timezone)).localize(parsed)
	return int(parsed.timestamp())
