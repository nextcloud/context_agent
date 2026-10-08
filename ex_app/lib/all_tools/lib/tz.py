# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime

import pytz


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


async def user_timezone_async(nc) -> str | None:
	try:
		user = (await nc.ocs('GET', '/ocs/v2.php/cloud/user')) or {}
		return user.get('timezone') or None
	except Exception:
		return None


def starts_at_timestamp(starts_at: str, timezone: str) -> int:
	parsed = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
	if parsed.tzinfo is None or parsed.utcoffset() is None:
		parsed = pytz.timezone(timezone).localize(parsed)
	return int(parsed.timestamp())
