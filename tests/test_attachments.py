# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bridge from a task's input_attachments to the tool runtime.

Run standalone:

	PYTHONPATH=. .venv/bin/python tests/test_attachments.py

or via pytest when available. No network: the task dicts are plain shapes as
they arrive on the tasks_provider API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.attachments import current_input_attachments, task_input_attachments  # noqa: E402


def test_file_ids_are_taken_over_in_order():
	task = {'input': {'input_attachments': [7, 3, 7]}}
	assert task_input_attachments(task) == (7, 3)


def test_junk_entries_are_skipped_not_fatal():
	task = {'input': {'input_attachments': ['abc', None, 5, '11', 0, -2, [1]]}}
	assert task_input_attachments(task) == (5, 11)


def test_missing_attachments_read_as_empty():
	assert task_input_attachments({}) == ()
	assert task_input_attachments({'input': {}}) == ()
	assert task_input_attachments({'input': {'input_attachments': []}}) == ()


def test_runtime_default_is_no_attachments():
	# Outside a task (the MCP server path) no conversation exists, so the
	# ContextVar must read as empty rather than raise or leak.
	assert current_input_attachments.get() == ()


def test_published_attachments_are_visible_and_resettable():
	token = current_input_attachments.set((4, 9))
	try:
		assert current_input_attachments.get() == (4, 9)
	finally:
		current_input_attachments.reset(token)
	assert current_input_attachments.get() == ()


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
