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
AsyncNextcloudApp's async capabilities property. For the marp deck task the
fake speaks the real output shape: one file id per selected format under
'slide_decks', in generation order, plus the deck source file. doc_gen derives its format
menu, its runtime guard for unservable formats and its availability from the
registered task types alone: a capability without a registered task type must
not advertise the formats of that app. The deck's image note is pinned to its
exact wording: the placed count is stated against the image-candidate total
when anything was omitted, and the omission reason appears exactly once. The
deck reply leads with that note plus a relay instruction (a plain generated
line when there is no note), so a compressing relay still keeps the count;
the labeled links follow as the remaining lines.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools import doc_gen, image_gen  # noqa: E402
from ex_app.lib.attachments import current_input_attachments  # noqa: E402

NEXTCLOUD_URL = 'https://nc.example.test'

SLIDEPREPPER_TASK_TYPE = 'slideprepper:slide_deck_generation'

RICHDOCUMENTS_TEXT_TASK_TYPE = 'richdocuments:text_to_text_document'
RICHDOCUMENTS_SPREADSHEET_TASK_TYPE = 'richdocuments:text_to_spreadsheet_document'
RICHDOCUMENTS_SLIDES_TASK_TYPE = 'richdocuments:slide_deck_generation'

# A full richdocuments instance: every format the tool knows is servable.
RICHDOCUMENTS_TASK_TYPES = {
	RICHDOCUMENTS_TEXT_TASK_TYPE: {},
	RICHDOCUMENTS_SPREADSHEET_TASK_TYPE: {},
	RICHDOCUMENTS_SLIDES_TASK_TYPE: {},
}


class _FakeFiles:
	"""Answers files.by_id lookups for the deck's attachment filtering.

	``entries`` maps file_id -> (mimetype, content_length); a file id that is
	missing from it resolves to None (deleted or inaccessible), one in
	``failing`` raises like a broken API call would.
	"""

	def __init__(self, entries=None, failing=()):
		self.entries = dict(entries or {})
		self.failing = set(failing)
		self.looked_up = []

	async def by_id(self, file_id):
		self.looked_up.append(file_id)
		if file_id in self.failing:
			raise RuntimeError(f'lookup exploded for {file_id}')
		entry = self.entries.get(file_id)
		if entry is None:
			return None
		mimetype, content_length = entry
		return SimpleNamespace(info=SimpleNamespace(mimetype=mimetype, content_length=content_length))


class _FakeNextcloudApp:
	"""Records ocs() calls; answers the endpoints the two tools touch."""

	def __init__(
		self,
		task_id=42,
		file_id=1337,
		deck_file_ids=(1337, 1338, 1339),
		source_file_id=1340,
		tasktypes=None,
		capabilities=None,
		files_entries=None,
		failing_files=(),
	):
		self.task_id = task_id
		self.file_id = file_id
		self.deck_file_ids = None if deck_file_ids is None else tuple(deck_file_ids)
		self.source_file_id = source_file_id
		# Defaults to a full richdocuments instance, so the default fake serves
		# every format; pass tasktypes explicitly for other instance shapes.
		self.tasktypes = dict(tasktypes if tasktypes is not None else RICHDOCUMENTS_TASK_TYPES)
		self._capabilities = dict(capabilities or {})
		self.files = _FakeFiles(files_entries, failing_files)
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
			# The marp deck task answers with one file id per selected output
			# format under 'slide_decks', in generation order pptx, html, pdf,
			# plus the markdown source under 'slide_deck_source'; deck_file_ids
			# None answers without the 'slide_decks' key, like a task that
			# broke its contract. Every other type answers with the single
			# output key it is mapped to.
			if json['type'] == SLIDEPREPPER_TASK_TYPE:
				output = {'slide_deck_source': self.source_file_id}
				if self.deck_file_ids is not None:
					output['slide_decks'] = list(self.deck_file_ids)
				return {'task': {'id': self.task_id, 'status': 'STATUS_SUCCESSFUL', 'output': output}}
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
	nc = _FakeNextcloudApp(task_id=3, deck_file_ids=(77, 78, 79), tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((method, endpoint, payload),) = _schedule_calls(nc)
	assert method == 'POST'
	assert endpoint == '/ocs/v1.php/taskprocessing/schedule'
	assert payload['appId'] == 'context_agent'
	assert payload['type'] == SLIDEPREPPER_TASK_TYPE
	assert payload['input'] == {'input': 'Deck brief'}
	# The task type's default output format applies, so none is sent
	assert 'output_format' not in payload['input']
	# The relay instruction leads; then one labeled link per generated file,
	# in the task's generation order. No attachments means no image note.
	assert reply == '\n'.join([
		'The deck was generated. Include every link below in your reply.',
		f'PowerPoint (pptx): {NEXTCLOUD_URL}/3/output-file/77/download',
		f'HTML: {NEXTCLOUD_URL}/3/output-file/78/download',
		f'PDF: {NEXTCLOUD_URL}/3/output-file/79/download',
	])


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
	nc = _FakeNextcloudApp(tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	assert asyncio.run(doc_gen.is_available(nc)) is True


def test_is_available_true_with_richdocuments_and_marp():
	nc = _FakeNextcloudApp(tasktypes={**RICHDOCUMENTS_TASK_TYPES, SLIDEPREPPER_TASK_TYPE: {}})
	assert asyncio.run(doc_gen.is_available(nc)) is True


def test_is_available_false_with_neither():
	nc = _FakeNextcloudApp(capabilities={}, tasktypes={'core:text2text': {}})
	assert asyncio.run(doc_gen.is_available(nc)) is False


def test_marp_only_instance_offers_only_the_slides_format():
	nc = _FakeNextcloudApp(tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
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
	nc = _FakeNextcloudApp(tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
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


class _ConversationAttachments:
	"""Sets the runtime bridge for one test call, restores it after.

	The bridge is a ContextVar the tool reads at call time, so the test only
	ever fakes what the task runtime would have published, never the tool
	interface itself.
	"""

	def __init__(self, *file_ids):
		self.file_ids = file_ids
		self._token = None

	def __enter__(self):
		self._token = current_input_attachments.set(self.file_ids)
		return self

	def __exit__(self, *_exc):
		current_input_attachments.reset(self._token)


PNG_1KB = ('image/png', 1024)
JPEG_1KB = ('image/jpeg', 1024)


def test_slides_payload_carries_conversation_images():
	nc = _FakeNextcloudApp(
		task_id=3,
		tasktypes={SLIDEPREPPER_TASK_TYPE: {}},
		files_entries={11: PNG_1KB, 12: JPEG_1KB},
	)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11, 12):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['type'] == SLIDEPREPPER_TASK_TYPE
	assert payload['input'] == {'input': 'Deck brief', 'images': [11, 12]}
	# The note leads, merged with the relay instruction into the first line;
	# the links follow, starting with the PowerPoint one on line two.
	assert reply.splitlines()[0] == (
		'2 attached images were included in the deck.'
		' Include this image count and every link below in your reply.'
	)
	assert reply.splitlines()[1] == f'PowerPoint (pptx): {NEXTCLOUD_URL}/3/output-file/1337/download'


def test_slides_without_attachments_payload_unchanged():
	nc = _FakeNextcloudApp(task_id=3, tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['input'] == {'input': 'Deck brief'}
	# No attachments means no images slot, no lookup traffic and no note,
	# so the lead is the plain generated line plus the relay instruction.
	assert 'images' not in payload['input']
	assert nc.files.looked_up == []
	assert 'attached image' not in reply
	assert reply == '\n'.join([
		'The deck was generated. Include every link below in your reply.',
		f'PowerPoint (pptx): {NEXTCLOUD_URL}/3/output-file/1337/download',
		f'HTML: {NEXTCLOUD_URL}/3/output-file/1338/download',
		f'PDF: {NEXTCLOUD_URL}/3/output-file/1339/download',
	])


def test_slides_unplaceable_image_said_aloud_pdf_unmentioned():
	# The SVG is an image the deck cannot place, so its omission is said
	# out loud against the candidate total of two; the PDF was never an
	# image for the deck and stays unmentioned, which that total encodes.
	nc = _FakeNextcloudApp(
		task_id=3,
		tasktypes={SLIDEPREPPER_TASK_TYPE: {}},
		files_entries={11: ('application/pdf', 5000), 12: PNG_1KB, 13: ('image/svg+xml', 500)},
	)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11, 12, 13):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['input'] == {'input': 'Deck brief', 'images': [12]}
	assert '1 of the 2 attached images was included in the deck.' in reply
	assert '1 attached image could not be included' in reply


def test_slides_webp_image_forwarded():
	# WebP is placeable since the deck task transcodes it on ingest, so
	# every attached image rides and no omission note is needed.
	nc = _FakeNextcloudApp(
		task_id=3,
		tasktypes={SLIDEPREPPER_TASK_TYPE: {}},
		files_entries={11: ('image/webp', 2048), 12: PNG_1KB},
	)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11, 12):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['input'] == {'input': 'Deck brief', 'images': [11, 12]}
	# The e2e shape: the count and the relay instruction lead as one
	# line, so a compressing relay drops them only against an order.
	assert reply.splitlines()[0] == (
		'2 attached images were included in the deck.'
		' Include this image count and every link below in your reply.'
	)
	assert reply.splitlines()[1] == f'PowerPoint (pptx): {NEXTCLOUD_URL}/3/output-file/1337/download'


def test_slides_oversized_image_not_forwarded_and_said_so():
	nc = _FakeNextcloudApp(
		task_id=3,
		tasktypes={SLIDEPREPPER_TASK_TYPE: {}},
		files_entries={11: ('image/png', 5 * 1024 * 1024 + 1), 12: JPEG_1KB},
	)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11, 12):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['input'] == {'input': 'Deck brief', 'images': [12]}
	assert '1 of the 2 attached images was included in the deck.' in reply
	assert (
		'1 attached image could not be included'
		' (only PNG, JPEG and WebP images up to 5 MB each can be placed in a deck).' in reply
	)


def test_slides_lookup_failure_is_fail_soft():
	# A broken lookup must not sink the deck: it schedules without the
	# unresolvable image and the result says the image is missing.
	nc = _FakeNextcloudApp(
		task_id=3,
		tasktypes={SLIDEPREPPER_TASK_TYPE: {}},
		files_entries={12: PNG_1KB},
		failing_files=(11,),
	)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11, 12):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['type'] == SLIDEPREPPER_TASK_TYPE
	assert payload['input'] == {'input': 'Deck brief', 'images': [12]}
	assert '1 of the 2 attached images was included in the deck.' in reply
	assert (
		'1 attached image could not be included'
		' (only PNG, JPEG and WebP images up to 5 MB each can be placed in a deck).' in reply
	)


def test_slides_all_lookups_failing_still_generates():
	nc = _FakeNextcloudApp(task_id=3, tasktypes={SLIDEPREPPER_TASK_TYPE: {}}, failing_files=(11, 12))
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11, 12):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	# The deck was still scheduled, without any images slot.
	assert payload['input'] == {'input': 'Deck brief'}
	assert (
		'2 attached images could not be included'
		' (only PNG, JPEG and WebP images up to 5 MB each can be placed in a deck).' in reply
	)


def test_slides_deleted_attachment_not_forwarded():
	# A file id that resolves to nothing could have been an image, so the
	# omission is said; the schedule goes out without it.
	nc = _FakeNextcloudApp(task_id=3, tasktypes={SLIDEPREPPER_TASK_TYPE: {}}, files_entries={12: PNG_1KB})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(99, 12):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['input'] == {'input': 'Deck brief', 'images': [12]}
	assert '1 of the 2 attached images was included in the deck.' in reply
	assert '1 attached image could not be included' in reply


def test_slides_fourth_deck_file_gets_the_fallback_label():
	# A fourth file is beyond the task's documented output formats, so its
	# link gets the plain label instead of a guessed format name; the links
	# start on line two, behind the relay instruction line.
	nc = _FakeNextcloudApp(task_id=3, deck_file_ids=(77, 78, 79, 80), tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	assert reply.splitlines()[0] == 'The deck was generated. Include every link below in your reply.'
	assert reply.splitlines()[1] == f'PowerPoint (pptx): {NEXTCLOUD_URL}/3/output-file/77/download'
	assert reply.splitlines()[4] == f'Slide deck: {NEXTCLOUD_URL}/3/output-file/80/download'


def test_slides_missing_deck_output_key_raises_a_clear_error():
	# A marp task result without 'slide_decks' must fail loudly, naming the
	# key and the task type, instead of collapsing into an empty reply.
	nc = _FakeNextcloudApp(task_id=3, deck_file_ids=None, tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	try:
		asyncio.run(generate_document(input='Deck brief', format='slides'))
	except Exception as e:
		assert 'slide_decks' in str(e), str(e)
		assert SLIDEPREPPER_TASK_TYPE in str(e), str(e)
	else:
		raise AssertionError('missing slide_decks output key did not raise')


def test_slides_attachments_do_not_reach_richdocuments_fallback():
	# The richdocuments deck task has no images slot, so attachments stay
	# with the tool runtime and the payload keeps its documented shape.
	nc = _FakeNextcloudApp(
		tasktypes={'core:text2text': {}, RICHDOCUMENTS_SLIDES_TASK_TYPE: {}},
		files_entries={11: PNG_1KB},
	)
	generate_document = _tool_coroutine(doc_gen, nc, 'generate_document')
	with _ConversationAttachments(11):
		reply = asyncio.run(generate_document(input='Deck brief', format='slides'))
	((_, _, payload),) = _schedule_calls(nc)
	assert payload['type'] == RICHDOCUMENTS_SLIDES_TASK_TYPE
	assert payload['input'] == {'text': 'Deck brief'}
	assert 'attached image' not in reply


def test_slides_docstring_teaches_the_attachment_contract():
	nc = _FakeNextcloudApp(tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	description = _tool_description(doc_gen, nc, 'generate_document')
	assert 'attached to the conversation are included in slide decks' in description
	assert 'no parameter for passing images' in description


def test_slides_docstring_teaches_relaying_the_image_count():
	# The e2e run showed the note losing against the model's own count
	# belief, so the contract must tell the model to repeat the tool's.
	nc = _FakeNextcloudApp(tasktypes={SLIDEPREPPER_TASK_TYPE: {}})
	description = _tool_description(doc_gen, nc, 'generate_document')
	assert 'states exactly how many attached images the deck contains' in description
	assert 'relay that count in your reply' in description
	assert 'may be fewer than were attached' in description


def test_slides_docstring_without_deck_task_mentions_no_images():
	nc = _FakeNextcloudApp(tasktypes={RICHDOCUMENTS_TEXT_TASK_TYPE: {}})
	description = _tool_description(doc_gen, nc, 'generate_document')
	assert 'slide decks' not in description


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
