# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""list_mails: the since filter and received_at.

Run standalone:

	PYTHONPATH=. python tests/test_mail_list_mails.py

or via pytest when available. The network layer is stubbed with a fake.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import mail  # noqa: E402

MESSAGES_PATH = '/ocs/v2.php/apps/mail/ocs/mailboxes/7/messages'
# 2026-11-24 07:30 UTC, 08:30 in Berlin (CET, +01:00)
NOV_24_0730_UTC = int(datetime(2026, 11, 24, 7, 30, tzinfo=timezone.utc).timestamp())

_user_counter = 0


class _FakeAsyncNc:
	"""Serves /cloud/user with the given profile timezone and records the Mail requests."""

	def __init__(self, profile_timezone, messages=None):
		global _user_counter
		_user_counter += 1
		# unique user per instance, so user_timezone_async's per-user cache does not leak between tests
		self._user_id = f"mail-test-{_user_counter}"
		self.profile_timezone = profile_timezone
		self.requests = []
		self.messages = messages or []

	@property
	def user(self):
		async def _u():
			return self._user_id

		return _u()

	async def ocs(self, method, path, json=None, **kwargs):
		if path == '/ocs/v2.php/cloud/user':
			return {'timezone': self.profile_timezone}
		self.requests.append({'method': method, 'path': path, 'json': json})
		return self.messages


def _call(nc, **kwargs):
	tools = {t.name: t for t in asyncio.run(mail.get_tools(nc))}
	return asyncio.run(tools['list_mails'].coroutine(folder_id=7, **kwargs))


def _list(nc, **kwargs):
	_call(nc, **kwargs)
	request, = nc.requests
	assert request['path'] == MESSAGES_PATH, request
	return request['json']


def test_without_since_sends_only_the_limit():
	assert _list(_FakeAsyncNc("Europe/Berlin"), n_mails=5) == {'limit': 5}


def test_since_with_offset_becomes_a_start_filter():
	body = _list(_FakeAsyncNc("Europe/Berlin"), since='2026-11-24T07:30:00Z')
	assert body == {'limit': 30, 'filter': f'start:{NOV_24_0730_UTC}'}, body


def test_naive_since_is_read_in_the_users_timezone():
	body = _list(_FakeAsyncNc("Europe/Berlin"), since='2026-11-24T08:30')
	assert body['filter'] == f'start:{NOV_24_0730_UTC}', body


def test_invalid_since_is_explained():
	try:
		_list(_FakeAsyncNc("Europe/Berlin"), since='yesterday')
	except ValueError as e:
		assert "since 'yesterday' is not an ISO datetime" in str(e), e
	else:
		raise AssertionError('expected a ValueError')


def test_naive_since_without_profile_timezone_asks_for_an_offset():
	try:
		_list(_FakeAsyncNc(None), since='2026-11-24T08:30')
	except ValueError as e:
		assert 'no UTC offset' in str(e) and 'profile has no timezone' in str(e), e
	else:
		raise AssertionError('expected a ValueError')


def test_messages_carry_received_at_in_the_users_timezone():
	messages = _call(_FakeAsyncNc("Europe/Berlin", [{'databaseId': 1, 'dateInt': NOV_24_0730_UTC}]))
	assert messages[0]['received_at'] == '2026-11-24T08:30:00+01:00', messages[0]


def test_without_profile_timezone_received_at_is_utc():
	messages = _call(_FakeAsyncNc(None, [{'databaseId': 1, 'dateInt': NOV_24_0730_UTC}]))
	assert messages[0]['received_at'] == '2026-11-24T07:30:00+00:00', messages[0]


if __name__ == '__main__':
	for name, fn in list(globals().items()):
		if name.startswith('test_') and callable(fn):
			fn()
			print(f'ok {name}')
