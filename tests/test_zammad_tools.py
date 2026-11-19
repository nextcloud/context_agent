# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Zammad tools: calls to integration_zammad's ticket API and the similar-ticket search.

Run standalone (``PYTHONPATH=. .venv/bin/python tests/test_zammad_tools.py``) or via pytest.
No network: a fake answers the OCS calls the tools make and the Context Chat search task.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nc_py_api import NextcloudException  # noqa: E402

from ex_app.lib.all_tools import zammad  # noqa: E402
from ex_app.lib.all_tools.lib.impulse import (  # noqa: E402
	CallClassification,
	ConfirmationPolicy,
	ImpulseRadius,
	Reversibility,
	classify_destructive,
	classify_item_count,
	classify_reversibility,
	classify_tool_call,
	is_always_confirmed,
)

ZAMMAD = 'https://zammad.example.com'
INSTANCE = zammad._instance_id(ZAMMAD)
OTHER_INSTANCE = zammad._instance_id('https://zammad.example.org')


TICKET = {
	'id': 1, 'number': '30001', 'title': 'Desktop client does not sync', 'state': 'open', 'url': f'{ZAMMAD}/#ticket/zoom/1',
	'articles': [
		{'id': 11, 'sender': 'Agent', 'internal': True, 'text': 'Asked for logs.'},
		{'id': 12, 'sender': 'Customer', 'internal': False, 'text': 'Sync stopped after the update to 3.14.'},
	],
}


class _Response:
	def __init__(self, data, status=200):
		self.text = json.dumps({'ocs': {'meta': {'status': 'failure', 'statuscode': status, 'message': ''}, 'data': data}})


class _FakeNextcloudApp:
	def __init__(self, sources=None, context_chat=True, capabilities=None, fail=None):
		self.sources = sources if sources is not None else []
		self.context_chat = context_chat
		self._capabilities = capabilities if capabilities is not None else {'integration_zammad': {'ticket_api': 2}}
		self.fail = fail
		self.calls = []

	@property
	async def capabilities(self):
		return self._capabilities

	async def ocs(self, method, path, json=None, params=None, **kwargs):
		self.calls.append((method, path, params, json))
		if path == '/ocs/v2.php/taskprocessing/tasktypes':
			types = {zammad.CONTEXT_CHAT_SEARCH_TASK_TYPE: {}} if self.context_chat else {}
			return {'types': types}
		if path.endswith('/taskprocessing/schedule'):
			return {'task': {'id': 1, 'status': 'STATUS_SUCCESSFUL', 'output': {'sources': self.sources}}}
		if '/taskprocessing/task/' in path:
			return {'task': {'id': 1, 'status': 'STATUS_SUCCESSFUL', 'output': {'sources': self.sources}}}
		if self.fail is not None:
			status, data = self.fail
			raise NextcloudException(status, reason='failure', response=_Response(data, status))
		if path == zammad.API + '/ticket':
			return TICKET
		if path == zammad.API + '/tickets':
			return {'tickets': [], 'count': 0}
		if path == zammad.API + '/ticket/notes' and method == 'POST':
			return {'id': 99, 'ticket_id': 1, 'number': '30001', 'title': TICKET['title'], 'created_at': None, 'url': f'{ZAMMAD}/#ticket/zoom/1/99'}
		raise AssertionError(f'Unexpected call {method} {path}')


def _source(instance, ticket_id):
	return json.dumps({'id': f'{zammad.CONTEXT_CHAT_PROVIDER}: {instance}-{ticket_id}', 'label': 'x', 'icon': '', 'url': ''})


async def _classify(tool, args, policy):
	return CallClassification(
		radius=await classify_tool_call(tool, args),
		destroys=await classify_destructive(tool, args),
		reversibility=await classify_reversibility(tool, args),
		item_count=await classify_item_count(tool, args, policy.count_limit),
		always=is_always_confirmed(tool),
	)


async def _tools(nc):
	return {t.name: t for t in await zammad.get_tools(nc)}


def test_instance_id_matches_integration_zammad():
	# the same test vector as InstanceIdTest in integration_zammad
	assert zammad._instance_id(' https://zammad.example.com/ ') == 'a24e71eb240131d42d05a36f8a0b32f5'


def test_similar_tickets_come_from_a_scoped_context_chat_search():
	sources = [
		_source(INSTANCE, 1),  # the ticket itself
		_source(INSTANCE, 3),
		_source(OTHER_INSTANCE, 2),  # another Zammad server
		_source(INSTANCE, 2),
		_source(INSTANCE, 3),  # a second chunk of the same ticket
		_source(INSTANCE, 5),
		json.dumps({'id': 'files__default: 123'}),
	]
	nc = _FakeNextcloudApp(sources=sources)
	result = asyncio.run(zammad.find_similar_tickets(nc, f'{ZAMMAD}/#ticket/zoom/1', 2))

	assert result == {'similar_tickets': [{'id': 3, 'url': f'{ZAMMAD}/#ticket/zoom/3'}, {'id': 2, 'url': f'{ZAMMAD}/#ticket/zoom/2'}]}
	# the search was scoped to tickets and used the title and the first customer message
	task_input = next(c for c in nc.calls if c[1].endswith('/taskprocessing/schedule'))[3]['input']
	assert task_input['scopeType'] == 'provider'
	assert task_input['scopeList'] == [zammad.CONTEXT_CHAT_PROVIDER]
	assert task_input['prompt'] == 'Desktop client does not sync\n\nSync stopped after the update to 3.14.'
	# only the ticket itself is read; the model opens the candidates it finds relevant
	assert [c[1] for c in nc.calls if c[1].startswith(zammad.API)] == [zammad.API + '/ticket']


def test_similar_tickets_without_candidates():
	nc = _FakeNextcloudApp(sources=[_source(INSTANCE, 1)])
	result = asyncio.run(zammad.find_similar_tickets(nc, '1'))
	assert result['similar_tickets'] == [] and result['reason']


def test_similar_tickets_without_context_chat():
	nc = _FakeNextcloudApp(context_chat=False)
	result = asyncio.run(zammad.find_similar_tickets(nc, '1'))
	assert result['similar_tickets'] == []
	assert 'Context Chat' in result['reason']


def test_errors_carry_the_message_for_the_user():
	nc = _FakeNextcloudApp(fail=(412, {'error': 'not_connected', 'message': 'Connect your account first.'}))
	tools = asyncio.run(_tools(nc))
	result = asyncio.run(tools['get_zammad_ticket'].ainvoke({'ticket': '1'}))
	assert result == {'error': 'not_connected', 'message': 'Connect your account first.'}


def test_list_passes_parameters():
	nc = _FakeNextcloudApp()
	tools = asyncio.run(_tools(nc))
	asyncio.run(tools['list_zammad_tickets'].ainvoke({'query': 'state.name:new', 'mine': True, 'limit': 50, 'sort_by': 'created_at', 'order_by': 'desc'}))
	method, path, params, _ = nc.calls[-1]
	assert (method, path) == ('GET', zammad.API + '/tickets')
	assert params == {'query': 'state.name:new', 'mine': 'true', 'limit': 20, 'sort_by': 'created_at', 'order_by': 'desc'}


def test_ticket_is_sized_to_the_tool_result_budget():
	from ex_app.lib.context_window import start_tool_result_budget

	async def run():
		nc = _FakeNextcloudApp()
		tools = await zammad.get_tools(nc)
		get_ticket = next(t for t in tools if t.name == 'get_zammad_ticket')
		await get_ticket.ainvoke({'ticket': '1'})
		without = dict(nc.calls[-1][2])
		budget = start_tool_result_budget()
		budget.chars = 35000
		await get_ticket.ainvoke({'ticket': '1'})
		return without, nc.calls[-1][2]

	without, with_budget = asyncio.run(run())
	assert 'max_chars' not in without
	assert with_budget['max_chars'] == 35000


def test_add_note_sends_the_ticket_number_but_no_footer():
	nc = _FakeNextcloudApp()
	tools = asyncio.run(_tools(nc))
	result = asyncio.run(tools['add_zammad_internal_note'].ainvoke({
		'ticket_title': 'Desktop client does not sync', 'ticket_number': '30001', 'ticket': f'{ZAMMAD}/#ticket/zoom/1',
		'text': 'Summary\n- proxy issue',
	}))
	method, path, _, body = nc.calls[-1]
	assert (method, path) == ('POST', zammad.API + '/ticket/notes')
	# the title is only for the user's confirmation; the footer is the server's job, never the model's
	assert body == {'ticket': f'{ZAMMAD}/#ticket/zoom/1', 'ticket_number': '30001', 'text': 'Summary\n- proxy issue', 'force': False}
	assert (result['number'], result['title']) == ('30001', 'Desktop client does not sync')


def test_note_arguments_put_the_ticket_title_first():
	# the confirmation dialog shows the first three arguments, so the user sees which ticket gets the note
	nc = _FakeNextcloudApp()
	tools = asyncio.run(_tools(nc))
	assert list(tools['add_zammad_internal_note'].args)[:3] == ['ticket_title', 'ticket_number', 'ticket']


def test_note_tool_only_with_the_notes_route():
	without = asyncio.run(_tools(_FakeNextcloudApp(capabilities={'integration_zammad': {'ticket_api': 1}})))
	assert 'add_zammad_internal_note' not in without
	assert {'list_zammad_tickets', 'get_zammad_ticket', 'find_similar_zammad_tickets'} <= set(without)
	assert 'add_zammad_internal_note' in asyncio.run(_tools(_FakeNextcloudApp()))


def test_classification():
	nc = _FakeNextcloudApp()
	tools = asyncio.run(_tools(nc))
	policy = ConfirmationPolicy()
	for name in ('list_zammad_tickets', 'get_zammad_ticket', 'find_similar_zammad_tickets'):
		classification = asyncio.run(_classify(tools[name], {}, policy))
		assert classification.radius == ImpulseRadius.SELF, name
		assert not policy.needs_confirmation([classification]), name
	note = asyncio.run(_classify(tools['add_zammad_internal_note'], {'ticket_title': 't', 'ticket_number': '1', 'ticket': '1', 'text': 'x'}, policy))
	assert note.radius == ImpulseRadius.GROUP
	assert note.reversibility == Reversibility.IRREVERSIBLE
	assert note.item_count == 1
	# with the default thresholds, the user confirms every note
	assert policy.needs_confirmation([note])


def test_availability():
	assert asyncio.run(zammad.is_available(_FakeNextcloudApp()))
	assert not asyncio.run(zammad.is_available(_FakeNextcloudApp(capabilities={})))


if __name__ == '__main__':
	for name, function in list(globals().items()):
		if name.startswith('test_') and callable(function):
			function()
			print(f'ok {name}')
