# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every built-in tool carries the markers the ConfirmationPolicy judges it by.

A tool missing a marker is not an error at runtime -- it silently falls back to
the worst case and is always confirmed -- so a forgotten decorator only shows up
as a needless prompt. This builds every tool module against a fake client and
checks that each tool has an impulse radius, a reversibility and an item count.

Run standalone (``python tests/test_tool_classification_markers.py``) or via pytest.
No network: the fake answers the few calls modules make while building their tools.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools.lib import impulse as impulse_lib  # noqa: E402
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, Reversibility  # noqa: E402
from ex_app.lib.tools import get_tool_module  # noqa: E402

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ex_app', 'lib', 'all_tools')

# MCP tools come from remote servers and cannot be decorated; they are confirmed
# as the worst case by design.
UNMARKABLE_MODULES = {'mcp'}


class _AllKeys(dict):
	def keys(self):
		return self

	def __contains__(self, _key):
		return True


class _FakeNextcloudApp(MagicMock):
	"""Answers what tool modules ask for while building their tools."""

	@property
	def user(self):
		async def user():
			return 'alice'
		return user()

	async def ocs(self, method, path, **_kwargs):
		if path == '/ocs/v2.php/search/providers':
			return [{'id': 'files', 'name': 'Files', 'filters': {'term': 'string'}}]
		if path == '/ocs/v2.php/taskprocessing/tasktypes':
			# Offer every task type, so modules build their optional tools too
			return {'types': _AllKeys()}
		if path.endswith('/assistant-folder-path'):
			return {'ocs': {'data': {'path': '/alice/files/Assistant'}}}
		return {}


def _tool_modules():
	files = sorted(f for f in os.listdir(TOOLS_DIR) if f.endswith('.py') and f != '__init__.py')
	for file in files:
		module_name, _, module = get_tool_module(file, TOOLS_DIR)
		if module_name not in UNMARKABLE_MODULES and hasattr(module, 'get_tools'):
			yield module_name, module


def _build_all_tools():
	async def build():
		built = {}
		# calendar opens a synchronous client for CalDAV, which needs a configured app
		with patch('ex_app.lib.all_tools.calendar.NextcloudApp', MagicMock):
			for module_name, module in _tool_modules():
				built[module_name] = await module.get_tools(_FakeNextcloudApp())
		return built
	return asyncio.run(build())


def _missing_markers(tool):
	action = impulse_lib._tool_action(tool)
	missing = []
	if not callable(getattr(action, impulse_lib._ATTR, None)):
		missing.append('impulse')
	marker = getattr(action, impulse_lib._REVERSIBILITY_ATTR, None)
	if not (isinstance(marker, Reversibility) or callable(marker)):
		missing.append('reversibility')
	marker = getattr(action, impulse_lib._ITEM_COUNT_ATTR, None)
	if not ((isinstance(marker, int) and not isinstance(marker, bool)) or callable(marker)):
		missing.append('item_count')
	return missing


def test_every_tool_has_all_classification_markers():
	unmarked = [
		f'{module_name}.{tool.name}: missing {", ".join(missing)}'
		for module_name, tools in _build_all_tools().items()
		for tool in tools
		if (missing := _missing_markers(tool))
	]
	assert not unmarked, 'Tools without classification markers:\n' + '\n'.join(unmarked)


def test_every_tool_module_contributes_tools():
	# A module that builds no tools against the fake would pass the check above vacuously.
	empty = [name for name, tools in _build_all_tools().items() if not tools]
	assert not empty, f'Tool modules that built no tools: {empty}'


def test_check_catches_an_unmarked_tool():
	from langchain_core.tools import tool

	@tool
	@impulse_lib.impulse(ImpulseRadius.SELF)
	async def half_marked(path: str):
		"""Has a radius but nothing else."""

	assert _missing_markers(half_marked) == ['reversibility', 'item_count']


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
