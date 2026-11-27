# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""list_board_cards: the cards Deck sends with null fields.

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


def test_cards_with_null_labels_and_assignees_are_listed():
	# Deck sends null instead of an empty list for archived cards
	card = _card(30, NEWEST)
	card['labels'] = None
	card['assignedUsers'] = None
	cards = _list(_FakeAsyncNc("Europe/Berlin", [{'id': 3, 'title': 'Old', 'cards': [card]}]), archived=True)
	assert cards[0]['labels'] == [] and cards[0]['assignees'] == [], cards[0]


if __name__ == '__main__':
	for name, fn in list(globals().items()):
		if name.startswith('test_') and callable(fn):
			fn()
			print(f'ok {name}')
