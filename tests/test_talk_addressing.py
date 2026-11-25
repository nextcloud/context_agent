# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who a Talk message is addressed to: conversation type, mentions, replies, own messages.

Run standalone:

	PYTHONPATH=. python tests/test_talk_addressing.py

or via pytest when available. The network layer is stubbed with fakes.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nc_py_api.talk import Conversation, ConversationType, TalkMessage  # noqa: E402

from ex_app.lib.all_tools import talk  # noqa: E402

USER = 'ben'


class _FakeTalk:
	def __init__(self, conversations, messages):
		self._conversations = conversations
		self._messages = messages

	async def get_user_conversations(self):
		return self._conversations

	async def receive_messages(self, conversation, look_in_future, limit):
		return self._messages


class _FakeAsyncNc:
	def __init__(self, conversations, messages=()):
		self.talk = _FakeTalk(conversations, [TalkMessage(m) for m in messages])

	@property
	def user(self):
		async def _u():
			return USER

		return _u()

	async def ocs(self, method, path, json=None):
		if path == '/ocs/v2.php/cloud/user':
			return {'timezone': 'UTC'}
		raise AssertionError(f"unexpected OCS call {method} {path}")


def _conversation(conversation_type, name='Team'):
	return Conversation({
		'displayName': name,
		'token': 'abc123',
		'type': conversation_type,
		'unreadMessages': 0,
		'lastActivity': 0,
	})


def _message(text, actor_id='anna', actor_name='Anna', params=None, parent=None, actor_type='users'):
	raw = {
		'id': 1,
		'timestamp': 0,
		'actorType': actor_type,
		'actorId': actor_id,
		'actorDisplayName': actor_name,
		'message': text,
		'messageParameters': params if params is not None else [],
		'isReplyable': True,
	}
	if parent is not None:
		raw['parent'] = parent
	return raw


def _messages(conversation_type, *messages):
	nc = _FakeAsyncNc([_conversation(conversation_type)], messages)
	tools = {t.name: t for t in asyncio.run(talk.get_tools(nc))}
	return json.loads(asyncio.run(tools['list_messages_in_conversation'].coroutine('Team')))


def _one(conversation_type, message):
	result = _messages(conversation_type, message)
	return result, result['messages'][0]


def test_conversation_types_are_words():
	nc = _FakeAsyncNc([
		_conversation(ConversationType.ONE_TO_ONE, 'Anna'),
		_conversation(ConversationType.GROUP, 'Team'),
		_conversation(ConversationType.NOTE_TO_SELF, 'Notes'),
	])
	tools = {t.name: t for t in asyncio.run(talk.get_tools(nc))}
	types = {c['name']: c['type'] for c in json.loads(asyncio.run(tools['list_talk_conversations'].coroutine()))}
	assert types == {'Anna': 'one-to-one', 'Team': 'group', 'Notes': 'note-to-self'}, types


def test_one_to_one_request_is_to_the_user():
	result, m = _one(ConversationType.ONE_TO_ONE, _message('Can you send me the slides?'))
	assert result['type'] == 'one-to-one', result
	assert result['you_are'] == USER, result
	assert m['by_you'] is False, m


def test_group_without_mention_is_not_to_the_user():
	result, m = _one(ConversationType.GROUP, _message('You should answer your emails'))
	assert result['type'] == 'group', result
	assert m['mentions_you'] is False, m
	assert m['reply_to'] is None and m['reply_to_you'] is False, m
	assert m['by_you'] is False, m


def test_user_mention_resolved_and_flagged():
	_, m = _one(ConversationType.GROUP, _message('{mention-user1} please review', params={
		'mention-user1': {'type': 'user', 'id': USER, 'name': 'Ben Bauer', 'mention-id': USER},
	}))
	assert m['message'] == '@Ben Bauer please review', m
	assert m['mentions_you'] is True, m


def test_mention_of_someone_else():
	_, m = _one(ConversationType.GROUP, _message('{mention-user1} you should answer your emails', params={
		'mention-user1': {'type': 'user', 'id': 'carl', 'name': 'Carl'},
	}))
	assert m['message'] == '@Carl you should answer your emails', m
	assert m['mentions_you'] is False, m


def test_federated_user_with_same_id_is_not_the_user():
	_, m = _one(ConversationType.GROUP, _message('{mention-federated-user1} hi', params={
		'mention-federated-user1': {'type': 'user', 'id': USER, 'name': 'Ben', 'server': 'https://other.example'},
	}))
	assert m['mentions_you'] is False, m


def test_mention_all():
	_, m = _one(ConversationType.GROUP, _message('{mention-call1} standup moved', params={
		'mention-call1': {'type': 'call', 'id': 'abc123', 'name': 'Team'},
	}))
	assert m['message'] == '@all standup moved', m
	assert m['mentions_you'] is True, m


def test_group_mention_left_to_the_model():
	_, m = _one(ConversationType.GROUP, _message('{mention-group1} deploy today', params={
		'mention-group1': {'type': 'user-group', 'id': 'devs', 'name': 'Developers'},
	}))
	assert m['message'] == '@Developers deploy today', m
	assert m['mentions_you'] == 'group', m


def test_reply_to_the_users_message():
	_, m = _one(ConversationType.GROUP, _message('Yes, do that', parent={
		'id': 0, 'actorType': 'users', 'actorId': USER, 'actorDisplayName': 'Ben Bauer', 'message': 'Shall I?',
	}))
	assert m['reply_to'] == 'Ben Bauer', m
	assert m['reply_to_you'] is True, m


def test_reply_to_someone_else():
	_, m = _one(ConversationType.GROUP, _message('Yes, do that', parent={
		'id': 0, 'actorType': 'users', 'actorId': 'carl', 'actorDisplayName': 'Carl', 'message': 'Shall I?',
	}))
	assert m['reply_to'] == 'Carl' and m['reply_to_you'] is False, m


def test_own_messages():
	_, m = _one(ConversationType.GROUP, _message('On it', actor_id=USER, actor_name='Ben Bauer'))
	assert m['by_you'] is True, m


def test_guest_with_users_id_is_not_the_user():
	_, m = _one(ConversationType.PUBLIC, _message('hi', actor_id=USER, actor_type='guests'))
	assert m['by_you'] is False, m


def test_shared_file_reads_as_its_name_and_keeps_its_id():
	_, m = _one(ConversationType.GROUP, _message('{file}', params={
		'actor': {'type': 'user', 'id': 'anna', 'name': 'Anna'},
		'file': {'type': 'file', 'id': '42', 'name': 'slides.odp'},
	}))
	assert m['message'] == 'slides.odp', m
	assert m['objects'] == [{'type': 'file', 'id': '42', 'name': 'slides.odp'}], m


def test_plain_message_has_no_objects():
	_, m = _one(ConversationType.GROUP, _message('hello'))
	assert 'objects' not in m, m


def test_note_to_self():
	result, _ = _one(ConversationType.NOTE_TO_SELF, _message('buy milk', actor_id=USER))
	assert result['type'] == 'note-to-self', result


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
