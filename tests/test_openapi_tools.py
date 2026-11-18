# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tools generated from the OpenAPI specs of apps without curated tools.

Run standalone (``python tests/test_openapi_tools.py``) or via pytest.
No network: a fake client answers the ocs_api_viewer and the generated calls.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nc_py_api.ex_app import SettingsField, SettingsFieldType, SettingsForm  # noqa: E402

from ex_app.lib.all_tools import openapi  # noqa: E402
from ex_app.lib.all_tools.lib import impulse as impulse_lib  # noqa: E402

SPEC = {
	'paths': {
		'/ocs/v2.php/apps/notes/api/{apiVersion}/notes/{id}': {
			'parameters': [
				{'name': 'apiVersion', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'enum': ['v1']}},
				{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}},
				{'name': 'OCS-APIRequest', 'in': 'header', 'required': True, 'schema': {'type': 'boolean'}},
			],
			'get': {
				'operationId': 'note-get',
				'summary': 'Get a note',
				'parameters': [{'name': 'tags[]', 'in': 'query', 'schema': {'type': 'array', 'items': {'type': 'string'}}}],
			},
			'put': {
				'operationId': 'note-update',
				'summary': 'Update a note',
				'requestBody': {
					'required': True,
					'content': {'application/json': {'schema': {'$ref': '#/components/schemas/NoteInput'}}},
				},
			},
			'delete': {'operationId': 'note-delete', 'deprecated': True},
		},
		'/ocs/v2.php/apps/notes/api/v1/attachments': {
			'post': {
				'operationId': 'note-get',
				'requestBody': {'content': {'multipart/form-data': {'schema': {'type': 'object'}}}},
			},
			'get': {'operationId': 'note-get'},
		},
	},
	'components': {
		'schemas': {
			'NoteInput': {
				'type': 'object',
				'required': ['title'],
				'properties': {
					'title': {'type': 'string'},
					'parent': {'$ref': '#/components/schemas/Note'},
				},
			},
			'Note': {
				'type': 'object',
				'properties': {'children': {'type': 'array', 'items': {'$ref': '#/components/schemas/Note'}}},
			},
		},
	},
}


def _operations():
	return {op.name: op for op in openapi.build_operations('notes', 'Notes', SPEC)}


def test_operations_are_built_from_the_spec():
	operations = _operations()
	# The deprecated deletion and the multipart upload are left out; the clashing name gets a suffix
	assert sorted(operations) == ['notes_note_get', 'notes_note_get_2', 'notes_note_update']

	get = operations['notes_note_get']
	assert get.description == 'Notes: Get a note'
	assert list(get.args_schema['properties']) == ['apiVersion', 'id', 'tags']
	assert get.args_schema['required'] == ['id']
	assert get.defaults == {'apiVersion': 'v1'}
	assert get.arguments['tags'] == ('query', 'tags[]')


def test_body_is_flattened_and_refs_are_resolved():
	update = _operations()['notes_note_update']
	assert update.arguments['title'] == ('body', 'title')
	assert update.args_schema['required'] == ['id', 'title']
	children = update.args_schema['properties']['parent']['properties']['children']
	# The recursive schema is cut off where it recurs
	assert children['items'] == {'type': 'object'}


class _Response:
	def __init__(self, status_code, payload):
		self.status_code = status_code
		self._payload = payload
		self.reason = 'Reason'
		self.headers = {'Content-Type': 'application/json'}

	def json(self):
		return self._payload


class _FakeNextcloudApp:
	def __init__(self, responses=None, config=None):
		self._session = MagicMock()
		self.appconfig_ex = MagicMock()
		self.ui = MagicMock()
		self.requests = []
		self.config = dict(config or {})
		self.registered = []
		responses = responses or {}
		self.app_cfg = SimpleNamespace(endpoint='https://cloud.example/nextcloud')

		async def request(method, url, **request_kwargs):
			self.requests.append((method, url, request_kwargs))
			return responses.get(url) or _Response(200, {'ocs': {'meta': {}, 'data': {'ok': True}}})

		self._session._create_adapter.return_value.request = request

		async def get_value(key, default=None):
			return self.config.get(key, default)

		async def set_value(key, value):
			self.config[key] = value

		async def register_form(form):
			self.registered.append(form)

		self.appconfig_ex.get_value = get_value
		self.appconfig_ex.set_value = set_value
		self.ui.settings.register_form = register_form

	@property
	def user(self):
		async def user():
			return 'alice'
		return user()


def test_call_fills_in_the_request_and_unwraps_the_ocs_envelope():
	nc = _FakeNextcloudApp()
	tool = openapi.make_tool(nc, 'https://cloud.example', _operations()['notes_note_update'])
	result = asyncio.run(tool.ainvoke({'id': 4, 'title': 'Hi', 'unknown': 1}))
	assert json.loads(result) == {'ok': True}
	method, url, kwargs = nc.requests[0]
	assert (method, url) == ('PUT', 'https://cloud.example/ocs/v2.php/apps/notes/api/v1/notes/4')
	assert kwargs['json'] == {'title': 'Hi'}
	assert kwargs['params'] == {'format': 'json'}


def test_errors_carry_the_ocs_message():
	url = 'https://cloud.example/ocs/v2.php/apps/notes/api/v1/notes/4'
	nc = _FakeNextcloudApp({url: _Response(404, {'ocs': {'meta': {'message': 'Note not found'}, 'data': []}})})
	tool = openapi.make_tool(nc, 'https://cloud.example', _operations()['notes_note_get'])
	try:
		asyncio.run(tool.ainvoke({'id': 4}))
	except ValueError as e:
		assert 'HTTP 404: Note not found' in str(e)
	else:
		raise AssertionError('expected the call to fail')


def test_only_reads_are_marked():
	nc = _FakeNextcloudApp()
	operations = _operations()
	read = openapi.make_tool(nc, '', operations['notes_note_get'])
	write = openapi.make_tool(nc, '', operations['notes_note_update'])
	assert impulse_lib.get_impulse_hook(read) is not None
	assert getattr(read.coroutine, impulse_lib._ITEM_COUNT_ATTR) == 0
	assert getattr(write.coroutine, impulse_lib._ATTR, None) is None


def test_detected_apps_are_offered_unchecked_and_only_checked_ones_get_tools():
	viewer = 'https://cloud.example/nextcloud' + openapi.VIEWER_PATH
	nc = _FakeNextcloudApp({
		viewer: _Response(200, [
			{'id': 'notes', 'name': 'Notes', 'version': '1', 'specs': ['notes', 'notes-administration']},
			{'id': 'deck', 'name': 'Deck', 'version': '1', 'specs': ['deck']},
			{'id': 'weather_status', 'name': 'Weather', 'version': '1', 'specs': ['weather_status-administration']},
		]),
		viewer + '/notes': _Response(200, SPEC),
	})
	form = SettingsForm(id='form', section_id='ai', title='Form', fields=[
		SettingsField(id=openapi.APP_STATUS_SETTING_ID, title='Apps', type=SettingsFieldType.MULTI_CHECKBOX, default={}),
	])
	openapi.use_settings_form(form)
	openapi._known_app_ids.clear()
	openapi._operations.clear()
	try:
		assert asyncio.run(openapi.get_tools(nc)) == []
		# Deck has curated tools, weather_status no default scope
		assert json.loads(nc.config[openapi.DETECTED_APPS_SETTING_ID]) == {'notes': 'Notes'}
		assert nc.registered[-1].fields[0].options == {'Notes (notes)': 'notes'}
		assert nc.registered[-1].fields[0].default == {'notes': False}

		asyncio.run(openapi.list_detected_apps.cache_invalidate(nc))
		nc.config[openapi.APP_STATUS_SETTING_ID] = json.dumps({'notes': True})
		tools = asyncio.run(openapi.get_tools(nc))
		assert sorted(tool.name for tool in tools) == ['notes_note_get', 'notes_note_get_2', 'notes_note_update']
		# Nothing new, so the form is not registered again
		assert len(nc.registered) == 1
	finally:
		openapi.use_settings_form(None)


if __name__ == '__main__':
	failed = 0
	for name, fn in sorted(globals().items()):
		if name.startswith('test_') and callable(fn):
			try:
				fn()
				print(f'PASS {name}')
			except AssertionError as exc:
				failed += 1
				print(f'FAIL {name}: {exc}')
	sys.exit(1 if failed else 0)
