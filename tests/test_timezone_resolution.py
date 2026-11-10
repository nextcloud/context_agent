# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timezone resolution in the scheduled task and calendar task tools.

Run standalone:

	PYTHONPATH=. python tests/test_timezone_resolution.py

or via pytest when available. The network layer is stubbed with fakes.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import assignments  # noqa: E402
from ex_app.lib.all_tools import calendar as cal  # noqa: E402
from ex_app.lib.all_tools.lib.tz import starts_at_timestamp, user_timezone_async  # noqa: E402

_user_counter = 0


class _FakeAsyncNc:
	"""Records assistant OCS calls; serves /cloud/user with the given profile timezone."""

	def __init__(self, profile_timezone):
		global _user_counter
		_user_counter += 1
		# unique user per instance, so user_timezone_async's per-user cache does not leak between tests
		self._user_id = f"tz-test-{_user_counter}"
		self.profile_timezone = profile_timezone
		self.calls = []

	@property
	def user(self):
		async def _u():
			return self._user_id

		return _u()

	async def ocs(self, method, path, json=None):
		if path == '/ocs/v2.php/cloud/user':
			return {'timezone': self.profile_timezone}
		self.calls.append((method, path, json))
		return True


def _assignment_tool(nc, name):
	tools = {t.name: t for t in asyncio.run(assignments.get_tools(nc))}
	return tools[name].coroutine


def test_update_without_timezone_leaves_stored_timezone_unchanged():
	nc = _FakeAsyncNc("Europe/Berlin")
	asyncio.run(_assignment_tool(nc, "update_scheduled_task")(id=1, prompt="Do Y"))
	(_, _, payload), = nc.calls
	assert payload['timezone'] is None, payload
	assert payload['startsAt'] is None, payload


def test_update_without_any_timezone_still_works():
	nc = _FakeAsyncNc(None)
	asyncio.run(_assignment_tool(nc, "update_scheduled_task")(id=1, prompt="Do Y"))
	assert nc.calls[0][2]['prompt'] == "Do Y"


def test_update_naive_starts_at_uses_profile_timezone():
	nc = _FakeAsyncNc("Europe/Berlin")
	asyncio.run(_assignment_tool(nc, "update_scheduled_task")(id=1, starts_at="2026-10-21T09:00:00"))
	payload = nc.calls[0][2]
	assert payload['startsAt'] == starts_at_timestamp("2026-10-21T07:00:00Z", None), payload
	assert payload['timezone'] is None, payload


def test_update_explicit_timezone_is_sent():
	nc = _FakeAsyncNc("Europe/Berlin")
	asyncio.run(_assignment_tool(nc, "update_scheduled_task")(id=1, timezone="Asia/Tokyo"))
	assert nc.calls[0][2]['timezone'] == "Asia/Tokyo"


def test_create_without_any_timezone_still_works():
	nc = _FakeAsyncNc(None)
	asyncio.run(_assignment_tool(nc, "create_scheduled_task")(title="T", prompt="Do X", recurrence_rule="FREQ=DAILY"))
	payload = nc.calls[0][2]
	assert payload['timezone'] is None, payload


def test_create_defaults_to_profile_timezone():
	nc = _FakeAsyncNc("Europe/Berlin")
	asyncio.run(_assignment_tool(nc, "create_scheduled_task")(title="T", prompt="Do X", recurrence_rule="FREQ=DAILY"))
	assert nc.calls[0][2]['timezone'] == "Europe/Berlin"


def test_create_naive_starts_at_without_any_timezone_raises():
	nc = _FakeAsyncNc(None)
	try:
		asyncio.run(_assignment_tool(nc, "create_scheduled_task")(title="T", prompt="Do X", recurrence_rule="", starts_at="2026-10-21T09:00:00"))
	except ValueError:
		return
	raise AssertionError("expected ValueError for a naive starts_at without a timezone")


def test_create_invalid_timezone_raises():
	nc = _FakeAsyncNc("Europe/Berlin")
	try:
		asyncio.run(_assignment_tool(nc, "create_scheduled_task")(title="T", prompt="Do X", recurrence_rule="", timezone="Not/AZone"))
	except ValueError:
		return
	raise AssertionError("expected ValueError for invalid timezone")


def test_starts_at_with_offset_needs_no_timezone():
	assert starts_at_timestamp("2026-10-21T09:00:00+02:00", None) == starts_at_timestamp("2026-10-21T07:00:00Z", None)


class _FakeCalendar:
	name = "Personal"

	def todos(self):
		return []


class _FakePrincipal:
	def calendars(self):
		return [_FakeCalendar()]


class _FakeCal:
	def principal(self):
		return _FakePrincipal()


class _FakeSyncNc:
	cal = _FakeCal()

	def set_user(self, user):
		pass

	@property
	def user(self):
		async def _u():
			return "tz-test-calendar"

		return _u()

	def ocs(self, method, path):
		return {'timezone': "Europe/Berlin"}


def _update_task_sync():
	original = cal.NextcloudApp
	cal.NextcloudApp = _FakeSyncNc
	try:
		tools = {t.name: t for t in asyncio.run(cal.get_tools(_FakeSyncNc()))}
	finally:
		cal.NextcloudApp = original
	return inspect.getclosurevars(tools["update_task"].coroutine).nonlocals["update_task_sync"]


def test_update_task_invalid_timezone_raises():
	try:
		_update_task_sync()("Personal", "uid", due_date="2026-11-01", timezone="Not/AZone")
	except ValueError:
		return
	raise AssertionError("expected ValueError for invalid timezone, not a swallowed False")


def test_update_task_without_due_date_ignores_timezone():
	assert _update_task_sync()("Personal", "uid", title="x", timezone="Not/AZone") is False


if __name__ == "__main__":
	failed = 0
	for name, fn in sorted(globals().items()):
		if name.startswith("test_") and callable(fn):
			try:
				fn()
				print(f"PASS {name}")
			except Exception as exc:
				failed += 1
				print(f"FAIL {name}: {type(exc).__name__}: {exc}")
	sys.exit(1 if failed else 0)
