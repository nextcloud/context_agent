# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The image_edit tool: input resolution, the scheduled task and the download link.

Run standalone:

	PYTHONPATH=. python tests/test_image_edit.py

or via pytest when available. The network layer is stubbed: the fake answers
the taskprocessing tasktypes and schedule endpoints and the app_api
nextcloud_url endpoint. Path lookups are replaced by a stub, because the real
one does a WebDAV PROPFIND.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import image_edit  # noqa: E402
from ex_app.lib.all_tools.lib import files as files_lib  # noqa: E402

NEXTCLOUD_URL = 'https://nc.example.test'
CORE_TYPE = 'core:image2image'


class _FakeNextcloudApp:
	"""Records ocs() calls; serves one immediately successful image2image task."""

	def __init__(self, tasktypes, task_id=42, output_file_id=1337):
		self.tasktypes = {tasktype: {} for tasktype in tasktypes}
		self.task_id = task_id
		self.output_file_id = output_file_id
		self.ocs_calls = []
		self.app_cfg = type('AppCfg', (), {'endpoint': 'http://nc.example.test'})()

	async def log(self, level, content):
		"""Sink for run_task's debug logging."""

	async def ocs(self, method, endpoint, json=None, **_kwargs):
		self.ocs_calls.append((method, endpoint, json))
		if endpoint == '/ocs/v2.php/taskprocessing/tasktypes':
			return {'types': self.tasktypes}
		if endpoint in (
			'/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute',
			# Asked by the host check of file links
			'/ocs/v1.php/apps/app_api/api/v1/info/nextcloud_url/absolute',
		):
			return {'absolute_url': NEXTCLOUD_URL}
		if endpoint == '/ocs/v1.php/taskprocessing/schedule':
			return {'task': {'id': self.task_id, 'status': 'STATUS_SUCCESSFUL', 'output': {'output': self.output_file_id}}}
		raise AssertionError(f'unexpected endpoint {endpoint}')


def _scheduled(nc):
	return [call[2] for call in nc.ocs_calls if call[1] == '/ocs/v1.php/taskprocessing/schedule']


def _edit_image(nc):
	tools = asyncio.run(image_edit.get_tools(nc))
	assert [tool.name for tool in tools] == ['edit_image'], tools
	return tools[0].coroutine


def test_core_type_is_scheduled_with_file_ids_from_links():
	nc = _FakeNextcloudApp([CORE_TYPE], task_id=9, output_file_id=61)
	link = asyncio.run(_edit_image(nc)(
		images=['https://nc.example.test/index.php/f/12', 'https://nc.example.test/f/34'],
		prompt='Make it look like a watercolor painting',
	))
	assert link == f'{NEXTCLOUD_URL}/9/output-file/61/download', link
	[scheduled] = _scheduled(nc)
	assert scheduled['type'] == CORE_TYPE, scheduled
	assert scheduled['input'] == {'input': [12, 34], 'prompt': 'Make it look like a watercolor painting'}, scheduled


def test_paths_are_resolved_to_file_ids():
	resolved = []

	async def fake_get_file_id_by_path(nc, path):
		resolved.append(path)
		return 77

	original = image_edit.get_file_id_by_path
	image_edit.get_file_id_by_path = fake_get_file_id_by_path
	try:
		nc = _FakeNextcloudApp([CORE_TYPE])
		asyncio.run(_edit_image(nc)(images=['/Photos/cat.jpg'], prompt='Add a hat'))
	finally:
		image_edit.get_file_id_by_path = original
	assert resolved == ['/Photos/cat.jpg'], resolved
	[scheduled] = _scheduled(nc)
	assert scheduled['input']['input'] == [77], scheduled


def test_file_ids_are_used_as_they_are():
	nc = _FakeNextcloudApp([CORE_TYPE])
	asyncio.run(_edit_image(nc)(images=['2810'], prompt='Add a hat'))
	[scheduled] = _scheduled(nc)
	assert scheduled['input']['input'] == [2810], scheduled
	# A file ID needs no lookup: the only requests are scheduling and the download link
	assert [call[1] for call in nc.ocs_calls if 'tasktypes' not in call[1]] == [
		'/ocs/v1.php/taskprocessing/schedule',
		'/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute',
	], nc.ocs_calls


def test_non_ascii_digits_are_paths_not_file_ids():
	resolved = []

	async def fake_get_file_id_by_path(nc, path):
		resolved.append(path)
		return 77

	original = image_edit.get_file_id_by_path
	image_edit.get_file_id_by_path = fake_get_file_id_by_path
	try:
		nc = _FakeNextcloudApp([CORE_TYPE])
		# Full-width digits: str.isdecimal() is true for them, but they are a name, not a file ID
		asyncio.run(_edit_image(nc)(images=['\uff12\uff10\uff12\uff14'], prompt='Add a hat'))
	finally:
		image_edit.get_file_id_by_path = original
	assert resolved == ['\uff12\uff10\uff12\uff14'], resolved
	[scheduled] = _scheduled(nc)
	assert scheduled['input']['input'] == [77], scheduled


def test_available_only_with_the_core_type():
	assert asyncio.run(image_edit.is_available(_FakeNextcloudApp([CORE_TYPE]))) is True
	# Before Nextcloud 36 only integration_openai's own type exists; the tool does not use it
	assert asyncio.run(image_edit.is_available(_FakeNextcloudApp(['integration_openai:image2image']))) is False
	assert asyncio.run(image_edit.is_available(_FakeNextcloudApp(['core:text2image']))) is False


class _FakePropfindApp:
	"""Answers one WebDAV PROPFIND with a file ID and records the requested URL."""

	class _Response:
		text = (
			'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
			'<d:response><d:propstat><d:prop><oc:fileid>99</oc:fileid></d:prop></d:propstat></d:response>'
			'</d:multistatus>'
		)

		def __init__(self, status_code=207):
			self.status_code = status_code

		def raise_for_status(self):
			if self.status_code >= 400:
				raise AssertionError(f'unexpected raise_for_status for {self.status_code}')

	def __init__(self, user_id='admin', missing_urls=()):
		self.user_id = user_id
		self.missing_urls = set(missing_urls)
		self.urls = []
		self.app_cfg = type('AppCfg', (), {'endpoint': 'http://nc.example.test'})()
		app = self

		class _Adapter:
			async def request(self, method, url, **_kwargs):
				app.urls.append(url)
				return _FakePropfindApp._Response(404 if url in app.missing_urls else 207)

		self._session = type('Session', (), {'_create_adapter': lambda _self, _dav: _Adapter()})()

	@property
	async def user(self):
		return self.user_id


def test_path_lookup_escapes_special_characters():
	base = 'http://nc.example.test/remote.php/dav/files'
	for path, expected in [
		('/Assistant/my house.png', f'{base}/admin/Assistant/my%20house.png'),
		# Unescaped, `#` would start a URL fragment and cut the path off
		('/Photos/cat #1?.jpg', f'{base}/admin/Photos/cat%20%231%3F.jpg'),
		# Unescaped, the server would decode %2e%2e into `..`
		('/x/%2e%2e/secret.jpg', f'{base}/admin/x/%252e%252e/secret.jpg'),
	]:
		nc = _FakePropfindApp()
		assert asyncio.run(files_lib.get_file_id_by_path(nc, path)) == 99
		assert nc.urls == [expected], (path, nc.urls)


def test_already_encoded_path_falls_back_to_the_decoded_name():
	base = 'http://nc.example.test/remote.php/dav/files/admin'
	# No file is literally named `my%20file.png`, so the decoded name is tried next
	nc = _FakePropfindApp(missing_urls=[f'{base}/my%2520file.png'])
	assert asyncio.run(files_lib.get_file_id_by_path(nc, '/my%20file.png')) == 99
	assert nc.urls == [f'{base}/my%2520file.png', f'{base}/my%20file.png'], nc.urls


def test_not_found_error_names_the_decoded_path_too():
	base = 'http://nc.example.test/remote.php/dav/files/admin'
	nc = _FakePropfindApp(missing_urls=[f'{base}/my%2520file.png', f'{base}/my%20file.png'])
	try:
		asyncio.run(files_lib.get_file_id_by_path(nc, '/my%20file.png'))
	except Exception as exc:
		assert str(exc) == 'File not found: /my%20file.png (also tried the decoded path /my file.png)', exc
	else:
		raise AssertionError('expected an exception')


def test_literal_percent_name_wins_over_the_decoded_one():
	base = 'http://nc.example.test/remote.php/dav/files/admin'
	nc = _FakePropfindApp()
	assert asyncio.run(files_lib.get_file_id_by_path(nc, '/100%25.png')) == 99
	assert nc.urls == [f'{base}/100%2525.png'], nc.urls


def test_decoded_fallback_rejects_traversal():
	base = 'http://nc.example.test/remote.php/dav/files/admin'
	nc = _FakePropfindApp(missing_urls=[f'{base}/x/%252e%252e/secret.jpg'])
	try:
		asyncio.run(files_lib.get_file_id_by_path(nc, '/x/%2e%2e/secret.jpg'))
	except ValueError as exc:
		assert 'traversal' in str(exc), exc
	else:
		raise AssertionError('expected a ValueError')
	assert nc.urls == [f'{base}/x/%252e%252e/secret.jpg'], nc.urls


def test_missing_path_is_reported_once():
	base = 'http://nc.example.test/remote.php/dav/files/admin'
	nc = _FakePropfindApp(missing_urls=[f'{base}/nope.png'])
	try:
		asyncio.run(files_lib.get_file_id_by_path(nc, '/nope.png'))
	except Exception as exc:
		assert 'not found' in str(exc), exc
	else:
		raise AssertionError('expected an exception')
	# Nothing to decode: no second request
	assert nc.urls == [f'{base}/nope.png'], nc.urls


def test_integer_file_ids_are_used_as_they_are():
	nc = _FakeNextcloudApp([CORE_TYPE])
	[tool] = asyncio.run(image_edit.get_tools(nc))
	# Through ainvoke, so the arguments pass the tool's schema validation as in the agent
	asyncio.run(tool.ainvoke({'images': [2810, '2811'], 'prompt': 'Add a hat'}))
	[scheduled] = _scheduled(nc)
	assert scheduled['input']['input'] == [2810, 2811], scheduled


def test_boolean_is_not_a_file_id():
	nc = _FakeNextcloudApp([CORE_TYPE])
	[tool] = asyncio.run(image_edit.get_tools(nc))
	try:
		asyncio.run(tool.ainvoke({'images': [True], 'prompt': 'Add a hat'}))
	except Exception as exc:
		assert 'images' in str(exc), exc
	else:
		raise AssertionError('expected a validation error')
	assert _scheduled(nc) == []


def test_links_to_other_servers_are_rejected():
	files_lib._nc_hosts = None
	nc = _FakeNextcloudApp([CORE_TYPE])
	try:
		asyncio.run(_edit_image(nc)(images=['https://other.example.test/index.php/f/12'], prompt='Add a hat'))
	except ValueError as exc:
		assert 'Not a file link of this Nextcloud' in str(exc), exc
	else:
		raise AssertionError('expected a ValueError')
	assert _scheduled(nc) == []


def test_blank_entries_are_rejected():
	for blank in ['', '   ']:
		nc = _FakeNextcloudApp([CORE_TYPE])
		try:
			asyncio.run(_edit_image(nc)(images=[blank], prompt='Add a hat'))
		except ValueError as exc:
			assert 'Empty image entry' in str(exc), exc
		else:
			raise AssertionError(f'expected a ValueError for {blank!r}')
		assert _scheduled(nc) == []


def test_surrounding_spaces_and_upper_case_schemes_are_tolerated():
	nc = _FakeNextcloudApp([CORE_TYPE])
	asyncio.run(_edit_image(nc)(images=[' 2810 ', 'HTTPS://nc.example.test/f/12'], prompt='Add a hat'))
	[scheduled] = _scheduled(nc)
	assert scheduled['input']['input'] == [2810, 12], scheduled


def test_path_lookup_escapes_the_user_id():
	nc = _FakePropfindApp(user_id='jane doe')
	asyncio.run(files_lib.get_file_id_by_path(nc, '/a.png'))
	assert nc.urls == ['http://nc.example.test/remote.php/dav/files/jane%20doe/a.png'], nc.urls


def test_no_images_is_rejected_before_scheduling():
	nc = _FakeNextcloudApp([CORE_TYPE])
	try:
		asyncio.run(_edit_image(nc)(images=[], prompt='Add a hat'))
	except ValueError as exc:
		assert 'at least one image' in str(exc), exc
	else:
		raise AssertionError('expected a ValueError')
	assert _scheduled(nc) == []


def test_path_lookup_rejects_traversal_before_any_request():
	nc = _FakeNextcloudApp([CORE_TYPE])
	try:
		asyncio.run(files_lib.get_file_id_by_path(nc, '/Photos/../../secret.jpg'))
	except ValueError as exc:
		assert 'traversal' in str(exc), exc
	else:
		raise AssertionError('expected a ValueError')
	assert nc.ocs_calls == []


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
