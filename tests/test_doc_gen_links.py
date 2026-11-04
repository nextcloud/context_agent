# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Download-link construction and slides dispatch in the doc_gen and image_gen tools.

Run standalone:

	PYTHONPATH=. .venv/bin/python tests/test_doc_gen_links.py

or via pytest when available. The network layer is stubbed: the fake answers
the app_api nextcloud_url endpoint with the dict the real OCS controller
returns ({'absolute_url': ...}), not a bare string. A fake that answered with
a bare string could not see the dict-repr mangling these tests guard against.
The fake also answers the taskprocessing tasktypes endpoint and mirrors
AsyncNextcloudApp's async capabilities property. doc_gen derives its format
menu, its runtime guard for unservable formats and its availability from the
registered task types alone: a capability without a registered task type must
not advertise the formats of that app.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import doc_gen, image_gen  # noqa: E402

NEXTCLOUD_URL = 'https://nc.example.test'

MARP_SLIDES_TASK_TYPE = 'marp_slides:slide_deck_generation'

RICHDOCUMENTS_TEXT_TASK_TYPE = 'richdocuments:text_to_text_document'
RICHDOCUMENTS_SPREADSHEET_TASK_TYPE = 'richdocuments:text_to_spreadsheet_document'
RICHDOCUMENTS_SLIDES_TASK_TYPE = 'richdocuments:slide_deck_generation'

# A full richdocuments instance: every format the tool knows is servable.
RICHDOCUMENTS_TASK_TYPES = {
	RICHDOCUMENTS_TEXT_TASK_TYPE: {},
	RICHDOCUMENTS_SPREADSHEET_TASK_TYPE: {},
	RICHDOCUMENTS_SLIDES_TASK_TYPE: {},
}


class _FakeNextcloudApp:
	"""Records ocs() calls; answers the endpoints the two tools touch."""

	def __init__(self, task_id=42, file_id=1337, tasktypes=None, capabilities=None):
		self.task_id = task_id
		self.file_id = file_id
		# Defaults to a full richdocuments instance, so the default fake serves
		# every format; pass tasktypes explicitly for other instance shapes.
		self.tasktypes = dict(tasktypes if tasktypes is not None else RICHDOCUMENTS_TASK_TYPES)
		self._capabilities = dict(capabilities or {})
		self.ocs_calls = []

	@property
	async def capabilities(self):
		# AsyncNextcloudApp.capabilities is an async property, so the fake must
		# return an awaitable here rather than the dict itself.
		return self._capabilities

	async def log(self, level, content):
		"""Sink for run_task's debug logging."""

	async def ocs(self, method, endpoint, json=None, **_kwargs):
		self.ocs_calls.append((method, endpoint, json))
		if endpoint == '/ocs/v2.php/taskprocessing/tasktypes':
			return {'types': self.tasktypes}
		if endpoint == '/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute':
			# The real endpoint (app_api OCSApiController) answers with a dict, not a bare string
			return {'absolute_url': NEXTCLOUD_URL}
		if endpoint == '/ocs/v1.php/taskprocessing/schedule':
			output_keys_by_type = {
				'richdocuments:slide_deck_generation': 'slide_deck',
				MARP_SLIDES_TASK_TYPE: 'slide_deck',
				'core:text2image': 'images',
			}
			output_key = output_keys_by_type.get(json['type'], 'file')
			if output_key == 'images':
				return {'task': {'id': self.task_id, 'status': 'STATUS_SUCCESSFUL', 'output': {'images': [self.file_id]}}}
			return {'task': {'id': self.task_id, 'status': 'STATUS_SUCCESSFUL', 'output': {output_key: self.file_id}}}
		raise AssertionError(f'unexpected ocs call: {method} {endpoint}')


def _tool_coroutine(module, nc, name):
	tools = asyncio.run(module.get_tools(nc))
	by_name = {tool.name: tool for tool in tools}
	return by_name[name].coroutine


def _tool_description(module, nc, name):
	tools = asyncio.run(module.get_tools(nc))
	by_name = {tool.name: tool for tool in tools}
	return by_name[name].description


def _schedule_calls(nc):
	return [call for call in nc.ocs_calls if call[1] == '/ocs/v1.php/taskprocessing/schedule']


def _assert_clean_link(link, expected):
	assert link == expected, link
	# The old bug embedded the whole OCS response dict in the f-string, so links
	# came out as {'absolute_url': '...'}/.../download with stray braces and quotes.
	assert '{' not in link and '}' not in link, link
	assert "'" not in link and 'absolute_url' not in link, link


def test_document_download_link_built_from_bare_absolute_url():
	nc = _FakeNextcloudApp(task_id=7, file_id=55)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	link = asyncio.run(generate_document(input='Quarterly report', format='text document'))
	_assert_clean_link(link, f'{NEXTCLOUD_URL}/7/output-file/55/download')


def test_slides_download_link_built_from_bare_absolute_url():
	nc = _FakeNextcloudApp(task_id=57, file_id=909)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	link = asyncio.run(generate_document(input='Topic', format='slides'))
	_assert_clean_link(link, f'{NEXTCLOUD_URL}/57/output-file/909/download')


def test_slides_dispatch_marp_task_type_when_registered():
	nc = _FakeNextcloudApp(task_id=3, file_id=77, tasktypes={MARP_SLIDES_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	link = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((method, endpoint, payload),) = _schedule_calls(nc)
	assert method == 'POST'
	assert endpoint == '/ocs/v1.php/taskprocessing/schedule'
	assert payload['appId'] == 'context_agent'
	assert payload['type'] == MARP_SLIDES_TASK_TYPE
	assert payload['input'] == {'prompt': 'Deck brief'}
	# The task type's default output format applies, so none is sent
	assert 'output_format' not in payload['input']
	_assert_clean_link(link, f'{NEXTCLOUD_URL}/3/output-file/77/download')


def test_slides_fall_back_to_richdocuments_without_marp_task_type():
	# Slides must be servable for the fallback to run at all, so the
	# richdocuments deck task type is registered while marp's is not.
	nc = _FakeNextcloudApp(tasktypes={'core:text2text': {}, RICHDOCUMENTS_SLIDES_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	link = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['type'] == 'richdocuments:slide_deck_generation'
	assert payload['input'] == {'text': 'Deck brief'}
	_assert_clean_link(link, f'{NEXTCLOUD_URL}/42/output-file/1337/download')


def test_is_available_true_with_richdocuments_only():
	# Availability comes from the registered task types, not from capabilities.
	nc = _FakeNextcloudApp(tasktypes=RICHDOCUMENTS_TASK_TYPES)
	assert asyncio.run(doc_gen.is_available(nc)) is True


def test_is_available_false_with_capability_but_no_registered_task_types():
	# Marcel's concern, encoded: the richdocuments capability alone proves
	# nothing. With none of its task types registered there is nothing the
	# tool could run, so it must not be offered.
	nc = _FakeNextcloudApp(capabilities={'richdocuments': {}}, tasktypes={})
	assert asyncio.run(doc_gen.is_available(nc)) is False


def test_is_available_true_with_marp_task_type_only():
	nc = _FakeNextcloudApp(tasktypes={MARP_SLIDES_TASK_TYPE: {}})
	assert asyncio.run(doc_gen.is_available(nc)) is True


def test_is_available_true_with_richdocuments_and_marp():
	nc = _FakeNextcloudApp(tasktypes={**RICHDOCUMENTS_TASK_TYPES, MARP_SLIDES_TASK_TYPE: {}})
	assert asyncio.run(doc_gen.is_available(nc)) is True


def test_is_available_false_with_neither():
	nc = _FakeNextcloudApp(capabilities={}, tasktypes={'core:text2text': {}})
	assert asyncio.run(doc_gen.is_available(nc)) is False


def test_marp_only_instance_offers_only_the_slides_format():
	nc = _FakeNextcloudApp(tasktypes={MARP_SLIDES_TASK_TYPE: {}})
	description = _tool_description(doc_gen, nc, 'generate_document')
	assert 'allowed values are "slides"' in description
	for unservable in ('"text document"', '"pdf"', '"spreadsheet"', '"excel spreadsheet"'):
		assert unservable not in description
	# The rest of the contract is untouched by the format filtering.
	assert ':param input:' in description
	assert ':return:' in description


def test_richdocuments_instance_offers_all_formats():
	nc = _FakeNextcloudApp()
	description = _tool_description(doc_gen, nc, 'generate_document')
	assert 'allowed values are "text document", "pdf", "spreadsheet", "excel spreadsheet" and "slides"' in description


def test_marp_only_instance_rejects_spreadsheet_request_softly():
	nc = _FakeNextcloudApp(tasktypes={MARP_SLIDES_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	reply = asyncio.run(generate_document(input='Budget plan', format='spreadsheet'))
	assert 'not available' in reply
	# The guard names what the instance can do instead.
	assert '"slides"' in reply
	# Nothing was scheduled for a format no provider can handle.
	assert _schedule_calls(nc) == []


def test_unknown_format_request_names_the_available_formats():
	nc = _FakeNextcloudApp()
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	reply = asyncio.run(generate_document(input='Budget plan', format='word document'))
	assert 'not available' in reply
	assert '"slides"' in reply
	assert _schedule_calls(nc) == []


def test_no_servable_formats_means_no_tools():
	nc = _FakeNextcloudApp(tasktypes={'core:text2text': {}})
	assert asyncio.run(doc_gen.get_tools(nc)) == []


def test_image_download_link_built_from_bare_absolute_url():
	nc = _FakeNextcloudApp(task_id=9, file_id=61)
	generate_image = _tool_coroutine(image_gen, nc, 'generate_image')
	link = asyncio.run(generate_image(input='A lighthouse at dusk'))
	_assert_clean_link(link, f'{NEXTCLOUD_URL}/9/output-file/61/download')


def test_fake_answers_the_dict_shape_the_endpoint_returns():
	# Guards the fake itself: if it regressed to a bare string, every link test
	# above would go blind to the dict-repr mangling.
	nc = _FakeNextcloudApp()

	async def fetch():
		return await nc.ocs('GET', '/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute', json={'url': 'x'})

	assert asyncio.run(fetch()) == {'absolute_url': NEXTCLOUD_URL}


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
