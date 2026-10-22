# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Download-link construction in the doc_gen and image_gen tools.

Run standalone:

	PYTHONPATH=. .venv/bin/python tests/test_doc_gen_links.py

or via pytest when available. The network layer is stubbed: the fake answers
the app_api nextcloud_url endpoint with the dict the real OCS controller
returns ({'absolute_url': ...}), not a bare string. A fake that answered with
a bare string could not see the dict-repr mangling these tests guard against.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import doc_gen, image_gen  # noqa: E402

NEXTCLOUD_URL = 'https://nc.example.test'


class _FakeNextcloudApp:
	"""Records ocs() calls; answers the endpoints the two tools touch."""

	def __init__(self, task_id=42, file_id=1337):
		self.task_id = task_id
		self.file_id = file_id
		self.ocs_calls = []

	async def log(self, level, content):
		"""Sink for run_task's debug logging."""

	async def ocs(self, method, endpoint, json=None, **_kwargs):
		self.ocs_calls.append((method, endpoint, json))
		if endpoint == '/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute':
			# The real endpoint (app_api OCSApiController) answers with a dict, not a bare string
			return {'absolute_url': NEXTCLOUD_URL}
		if endpoint == '/ocs/v1.php/taskprocessing/schedule':
			output_keys_by_type = {
				'richdocuments:slide_deck_generation': 'slide_deck',
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
