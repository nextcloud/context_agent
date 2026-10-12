# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Payload-level tests for schedule_event serialization.

Run standalone (needs the AppAPI env, e.g. `set -a; source test.env; set +a`):

	PYTHONPATH=. .venv/bin/python tests/test_schedule_event_payload.py

or via pytest when available. The network layer is stubbed: NextcloudApp inside
get_tools is replaced with a fake, and the ICS payload handed to CalDAV
add_event is captured. This is the layer the all-day regression (PR 257 review)
lived at — a dav roundtrip only proves an event got created, not its shape.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
	import pytest
except ImportError:  # standalone mode, see __main__ block
	pytest = None

from ex_app.lib.all_tools import calendar as cal  # noqa: E402

CAPTURED = {}


class _FakeCalendar:
	name = "Personal"

	def add_event(self, ics):
		CAPTURED["ics"] = ics


class _FakePrincipal:
	def calendars(self):
		return [_FakeCalendar()]


class _FakeCal:
	def principal(self):
		return _FakePrincipal()


class _FakeNc:
	cal = _CalNS = None  # placeholder replaced below


class _FakeNcImpl:
	cal = None

	def set_user(self, user):
		pass

	@property
	def user(self):
		async def _u():
			return "payload-test"

		return _u()

	def ocs(self, method, path):
		return {
			"email": "payload-test@example.com",
			"displayname": "Payload Test",
			"timezone": "Europe/Berlin",
		}


_FakeNcImpl.cal = _FakeCal()


def _schedule_event_sync():
	cal.NextcloudApp = lambda: _FakeNcImpl()
	tools = asyncio.run(cal.get_tools(_FakeNcImpl()))
	by_name = {t.name: t for t in tools}
	wrapper = getattr(by_name["schedule_event"], "coroutine", None) or by_name["schedule_event"].func
	return inspect.getclosurevars(wrapper).nonlocals["schedule_event_sync"]


def _schedule(**kwargs):
	CAPTURED.clear()
	args = {
		"calendar_name": "Personal",
		"title": "Payload test",
		"description": "n/a",
		"start_date": "2026-10-17",
		"end_date": "2026-10-17",
		"attendees": None,
		"start_time": None,
		"end_time": None,
		"location": None,
		"timezone": "Europe/Berlin",
	}
	args.update(kwargs)
	_schedule_event_sync()(**args)
	return CAPTURED["ics"]


def _unfold(ics):
	"""icalendar line-folds long values; join continuation lines before asserting."""
	lines = ics.splitlines()
	out = []
	for line in lines:
		if line[:1] in (" ", "\t") and out:
			out[-1] += line[1:]
		else:
			out.append(line)
	return "\r\n".join(out)


def test_all_day_event_uses_date_values():
	ics = _unfold(_schedule())
	assert "DTSTART;VALUE=DATE:20261017" in ics, ics
	assert "DTEND;VALUE=DATE:20261018" in ics, ics  # exclusive end: next day
	assert "TZID=" not in ics, ics
	assert "T000000" not in ics, ics


def test_timed_event_keeps_tzid():
	ics = _unfold(_schedule(start_time="12:00 PM", end_time="1:00 PM"))
	assert "DTSTART;TZID=Europe/Berlin:20261017T120000" in ics, ics
	assert "DTEND;TZID=Europe/Berlin:20261017T130000" in ics, ics
	assert "VALUE=DATE" not in ics, ics


def test_invalid_timezone_raises():
	try:
		_schedule(timezone="Not/AZone")
	except ValueError:
		return
	raise AssertionError("expected ValueError for invalid timezone")


def test_omitted_timezone_falls_back_to_profile():
	ics = _unfold(_schedule(timezone=None))  # fake profile carries Europe/Berlin
	assert "DTSTART;VALUE=DATE:20261017" in ics, ics


if __name__ == "__main__":
	if not os.environ.get("NEXTCLOUD_URL"):
		print("needs AppAPI env: set -a; source test.env; set +a")
		sys.exit(77)
	failed = 0
	for name, fn in sorted(globals().items()):
		if name.startswith("test_") and callable(fn):
			try:
				fn()
				print(f"PASS {name}")
			except AssertionError as exc:
				failed += 1
				print(f"FAIL {name}: {exc}")
	sys.exit(1 if failed else 0)
