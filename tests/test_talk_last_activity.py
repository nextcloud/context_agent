# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Last activity in list_talk_conversations.

Run standalone:

	PYTHONPATH=. python tests/test_talk_last_activity.py

or via pytest when available. The network layer is stubbed with fakes.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nc_py_api.talk import Conversation, ConversationType  # noqa: E402

from ex_app.lib.all_tools import talk  # noqa: E402

# 2026-11-24 07:30 UTC, 08:30 in Berlin (CET, +01:00)
LAST_ACTIVITY = int(datetime(2026, 11, 24, 7, 30, tzinfo=timezone.utc).timestamp())

_user_counter = 0


class _FakeTalk:
	def __init__(self, conversations):
		self._conversations = conversations

	async def get_user_conversations(self):
		return self._conversations


class _FakeAsyncNc:
	"""Serves /cloud/user with the given profile timezone and Talk with the given conversations."""

	def __init__(self, profile_timezone, conversations):
		global _user_counter
		_user_counter += 1
		# unique user per instance, so user_timezone_async's per-user cache does not leak between tests
		self._user_id = f"talk-test-{_user_counter}"
		self.profile_timezone = profile_timezone
		self.talk = _FakeTalk(conversations)

	@property
	def user(self):
		async def _u():
			return self._user_id

		return _u()

	async def ocs(self, method, path, json=None):
		if path == '/ocs/v2.php/cloud/user':
			return {'timezone': self.profile_timezone}
		raise AssertionError(f"unexpected OCS call {method} {path}")


def _read_conversation():
	return Conversation({
		'displayName': 'Team',
		'token': 'abc123',
		'type': ConversationType.GROUP,
		'unreadMessages': 0,
		'lastActivity': LAST_ACTIVITY,
	})


def _list(nc):
	tools = {t.name: t for t in asyncio.run(talk.get_tools(nc))}
	return json.loads(asyncio.run(tools['list_talk_conversations'].coroutine()))


def test_read_conversation_still_shows_last_activity():
	conv, = _list(_FakeAsyncNc("Europe/Berlin", [_read_conversation()]))
	assert conv['unread_messages'] == 0, conv
	assert datetime.fromisoformat(conv['last_activity']).timestamp() == LAST_ACTIVITY, conv


def test_last_activity_in_user_timezone():
	conv, = _list(_FakeAsyncNc("Europe/Berlin", [_read_conversation()]))
	assert conv['last_activity'] == "2026-11-24T08:30+01:00", conv


def test_last_activity_without_profile_timezone_is_utc():
	conv, = _list(_FakeAsyncNc(None, [_read_conversation()]))
	assert conv['last_activity'] == "2026-11-24T07:30+00:00", conv


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
