# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""list_board_cards: modified_since, include_description and the cards Deck sends with null fields.

Run standalone:

	PYTHONPATH=. python tests/test_deck_list_board_cards.py

or via pytest when available. The network layer is stubbed with fakes.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import deck  # noqa: E402

# 2026-11-24 07:30 UTC, 08:30 in Berlin (CET, +01:00)
OLD = int(datetime(2026, 11, 24, 7, 30, tzinfo=timezone.utc).timestamp())
# 2026-11-25 07:30 UTC
NEWER = int(datetime(2026, 11, 25, 7, 30, tzinfo=timezone.utc).timestamp())
# 2026-11-26 07:30 UTC
NEWEST = int(datetime(2026, 11, 26, 7, 30, tzinfo=timezone.utc).timestamp())

_user_counter = 0


def _card(card_id, last_modified, description='long text'):
	return {
		'id': card_id,
		'title': f'Card {card_id}',
		'description': description,
		'labels': [],
		'assignedUsers': [],
		'duedate': None,
		'archived': False,
		'done': None,
		'commentsCount': 0,
		'lastModified': last_modified,
	}


def _stacks():
	return [
		{'id': 1, 'title': 'To do', 'cards': [_card(10, OLD), _card(11, NEWEST)]},
		{'id': 2, 'title': 'Done', 'cards': [_card(20, NEWER)]},
	]


class _FakeResponse:
	status_code = 200
	reason_phrase = 'OK'

	def __init__(self, body):
		self._body = body

	def json(self):
		return self._body


class _FakeAdapter:
	def __init__(self, nc):
		self._nc = nc

	async def request(self, method, url, headers=None, json=None):
		self._nc.requests.append({'method': method, 'url': url, 'headers': headers})
		return _FakeResponse(self._nc.stacks)


class _FakeAsyncNc:
	"""Serves /cloud/user with the given profile timezone and the Deck stacks with the given cards."""

	def __init__(self, profile_timezone, stacks):
		global _user_counter
		_user_counter += 1
		# unique user per instance, so user_timezone_async's per-user cache does not leak between tests
		self._user_id = f"deck-test-{_user_counter}"
		self.profile_timezone = profile_timezone
		self.stacks = stacks
		self.requests = []
		self.app_cfg = SimpleNamespace(endpoint='http://nc.test')
		self._session = SimpleNamespace(adapter=_FakeAdapter(self))

	@property
	def user(self):
		async def _u():
			return self._user_id

		return _u()

	async def ocs(self, method, path, json=None):
		if path == '/ocs/v2.php/cloud/user':
			return {'timezone': self.profile_timezone}
		raise AssertionError(f"unexpected OCS call {method} {path}")


def _list(nc, **kwargs):
	tools = {t.name: t for t in asyncio.run(deck.get_tools(nc))}
	return json.loads(asyncio.run(tools['list_board_cards'].coroutine(board_id=5, **kwargs)))


def test_without_modified_since_lists_every_card_in_board_order():
	nc = _FakeAsyncNc("Europe/Berlin", _stacks())
	cards = _list(nc)
	assert [c['id'] for c in cards] == [10, 11, 20], cards
	assert 'If-Modified-Since' not in nc.requests[0]['headers'], nc.requests


def test_cards_carry_last_modified_in_the_users_timezone():
	cards = _list(_FakeAsyncNc("Europe/Berlin", _stacks()))
	assert cards[0]['last_modified'] == '2026-11-24T08:30:00+01:00', cards[0]


def test_last_modified_round_trips_as_modified_since():
	# a caller that keeps the newest last_modified as its cursor must not get that card again
	newest, = _list(_FakeAsyncNc("Europe/Berlin", _stacks()), modified_since='2026-11-25T12:00:00Z')
	assert _list(_FakeAsyncNc("Europe/Berlin", _stacks()), modified_since=newest['last_modified']) == []


def test_modified_since_keeps_newer_cards_newest_first_and_sends_the_header():
	nc = _FakeAsyncNc("Europe/Berlin", _stacks())
	cards = _list(nc, modified_since='2026-11-24T12:00:00+00:00')
	assert [c['id'] for c in cards] == [11, 20], cards
	assert nc.requests[0]['headers']['If-Modified-Since'] == 'Tue, 24 Nov 2026 12:00:00 GMT', nc.requests


def test_naive_modified_since_is_read_in_the_users_timezone():
	# 08:30 Berlin is exactly OLD, which is not "after" it
	cards = _list(_FakeAsyncNc("Europe/Berlin", _stacks()), modified_since='2026-11-24T08:30')
	assert [c['id'] for c in cards] == [11, 20], cards


def test_modified_since_also_filters_archived_cards():
	# the archived endpoint ignores If-Modified-Since, so the filter has to happen here too
	cards = _list(_FakeAsyncNc("Europe/Berlin", _stacks()), archived=True, modified_since='2026-11-25T12:00:00Z')
	assert [c['id'] for c in cards] == [11], cards


def test_cards_with_null_labels_and_assignees_are_listed():
	# Deck sends null instead of an empty list for archived cards
	card = _card(30, NEWEST)
	card['labels'] = None
	card['assignedUsers'] = None
	cards = _list(_FakeAsyncNc("Europe/Berlin", [{'id': 3, 'title': 'Old', 'cards': [card]}]), archived=True)
	assert cards[0]['labels'] == [] and cards[0]['assignees'] == [], cards[0]


def test_include_description_false_leaves_descriptions_out():
	cards = _list(_FakeAsyncNc("Europe/Berlin", _stacks()), include_description=False)
	assert all('description' not in c for c in cards), cards
	assert cards[0]['title'] == 'Card 10', cards[0]


def test_invalid_modified_since_is_explained():
	try:
		_list(_FakeAsyncNc("Europe/Berlin", _stacks()), modified_since='yesterday')
	except ValueError as e:
		assert "'yesterday'" in str(e) and 'not an ISO datetime' in str(e), e
	else:
		raise AssertionError('expected a ValueError')


def test_naive_modified_since_without_profile_timezone_asks_for_an_offset():
	try:
		_list(_FakeAsyncNc(None, _stacks()), modified_since='2026-11-24T08:30')
	except ValueError as e:
		assert 'no UTC offset' in str(e) and "profile has no timezone" in str(e), e
	else:
		raise AssertionError('expected a ValueError')


def test_without_profile_timezone_last_modified_is_utc():
	cards = _list(_FakeAsyncNc(None, _stacks()))
	assert cards[0]['last_modified'] == '2026-11-24T07:30:00+00:00', cards[0]


if __name__ == '__main__':
	for name, fn in list(globals().items()):
		if name.startswith('test_') and callable(fn):
			fn()
			print(f'ok {name}')
