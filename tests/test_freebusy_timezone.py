# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timezone handling of the free/busy range used by find_free_time_slot_in_calendar.

Run standalone:

	PYTHONPATH=. python tests/test_freebusy_timezone.py

or via pytest when available.
"""
import os
import sys
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools.lib.freebusy_finder import find_available_slots, freebusy_range  # noqa: E402

BERLIN = pytz.timezone("Europe/Berlin")
# fixed "now" before all test dates, so the tests do not depend on when they are run
NOW = BERLIN.localize(datetime(2026, 10, 1, 10, 0))


def test_dates_are_midnight_in_user_timezone():
	start, end = freebusy_range("2026-11-12", "2026-11-14", BERLIN, now=NOW)
	assert start.astimezone(pytz.UTC) == pytz.UTC.localize(datetime(2026, 11, 11, 23, 0)), start
	# the end date is inclusive
	assert end.astimezone(pytz.UTC) == pytz.UTC.localize(datetime(2026, 11, 14, 23, 0)), end


def test_default_end_keeps_wall_clock_across_dst():
	# DST ends in Berlin on 2026-10-25
	start, end = freebusy_range("2026-10-22", None, BERLIN, now=NOW)
	assert end.replace(tzinfo=None) == datetime(2026, 10, 29), end
	assert end.utcoffset() == timedelta(hours=1), end


def test_end_before_start_falls_back_to_seven_days():
	start, end = freebusy_range("2026-11-12", "2026-11-10", BERLIN, now=NOW)
	assert end.replace(tzinfo=None) == datetime(2026, 11, 19), end


def test_single_day_covers_whole_day():
	start, end = freebusy_range("2026-11-12", "2026-11-12", BERLIN, now=NOW)
	assert start.replace(tzinfo=None) == datetime(2026, 11, 12), start
	assert end.replace(tzinfo=None) == datetime(2026, 11, 13), end


def test_start_in_the_past_is_clamped_to_now():
	start, end = freebusy_range("2026-09-01", "2026-10-01", BERLIN, now=NOW)
	assert start == NOW, start
	assert end.replace(tzinfo=None) == datetime(2026, 10, 2), end


def test_default_start_is_in_user_timezone():
	kathmandu = pytz.timezone("Asia/Kathmandu")  # UTC+5:45
	start, _ = freebusy_range(None, None, kathmandu)
	assert start.minute in (0, 30), start
	assert start.utcoffset() == timedelta(hours=5, minutes=45), start


def test_slots_respect_utc_busy_times_in_local_range():
	start, end = freebusy_range("2026-11-12", "2026-11-13", BERLIN, now=NOW)
	# busy 00:00-08:00 Berlin, given in UTC as the CalDAV server returns it
	busy = [(pytz.UTC.localize(datetime(2026, 11, 11, 23)), pytz.UTC.localize(datetime(2026, 11, 12, 7)))]
	(slot_start, _), *_ = find_available_slots(start, end, busy)
	assert slot_start.astimezone(BERLIN).isoformat() == "2026-11-12T08:00:00+01:00", slot_start


if __name__ == "__main__":
	for name, fn in list(globals().items()):
		if name.startswith("test_") and callable(fn):
			fn()
			print(f"ok  {name}")
