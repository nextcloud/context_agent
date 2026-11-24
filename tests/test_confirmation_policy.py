# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Classification hooks and the ConfirmationPolicy that judges them.

Runnable standalone (``python tests/test_confirmation_policy.py``) or via pytest.
No network: tools are plain LangChain tools with constant or local hooks.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool  # noqa: E402
from nc_py_api import NextcloudException  # noqa: E402

import ex_app.lib.all_tools.lib.impulse as impulse_module  # noqa: E402
from ex_app.lib.all_tools.lib.impulse import (  # noqa: E402
	ALLOWED_CALL,
	CallClassification,
	ConfirmationPolicy,
	ImpulseRadius,
	Reversibility,
	always_allow,
	classify_destructive,
	classify_item_count,
	classify_reversibility,
	classify_tool_call,
	destructive,
	destructive_if,
	from_mcp_annotations,
	impulse,
	is_always_allowed,
	item_count,
	read_only,
	reversibility,
)
from ex_app.lib.all_tools.mcp import parse_always_allow, split_trusted_servers  # noqa: E402


@tool
@impulse(ImpulseRadius.SELF)
@read_only
async def read_note(path: str):
	"""Read a note."""


async def _tree_size(path=None, count_limit=None):
	# a "folder" of 1000 items, counted one by one up to the limit
	counted = 0
	for _ in range(1000):
		counted += 1
		if counted >= count_limit:
			break
	return counted


@tool
@impulse(ImpulseRadius.SELF)
@destructive
@reversibility(Reversibility.RECOVERABLE)
@item_count(_tree_size)
async def trash_folder(path: str):
	"""Trash a folder."""


def _broken(**_kwargs):
	raise RuntimeError('lookup failed')


@tool
@impulse(ImpulseRadius.SELF)
@reversibility(_broken)
@item_count(_broken)
async def flaky(path: str):
	"""A tool whose hooks fail."""


@tool
async def undecorated(path: str):
	"""Like an MCP tool: no markers at all."""


class _FailsOnce:
	"""A lookup the server throttles the first time it is asked."""

	def __init__(self, answer):
		self.answer = answer
		self.calls = 0

	async def __call__(self, path=None):
		self.calls += 1
		if self.calls == 1:
			raise NextcloudException(429, 'Too Many Requests')
		return self.answer


_radius_once = _FailsOnce(ImpulseRadius.SELF)
_destroys_once = _FailsOnce(False)


@tool
@impulse(_radius_once)
@destructive_if(_destroys_once)
async def write_private_note(path: str):
	"""Write into a private folder, with lookups that fail once."""


_overloaded_calls = []


def _overloaded(path=None):
	_overloaded_calls.append(path)
	raise NextcloudException(503, 'Service Unavailable')


@tool
@impulse(_overloaded)
async def unreachable(path: str):
	"""A tool whose radius lookup always meets an overloaded server."""


_unanswerable_calls = []


def _unanswerable(path=None):
	_unanswerable_calls.append(path)
	raise ValueError('No path to determine the audience of')


@tool
@impulse(_unanswerable)
async def unanswerable(path: str):
	"""A tool whose radius lookup cannot answer, however often it is asked."""


@pytest.fixture
def no_retry_pause(monkeypatch):
	"""No real pauses: a failing hook is retried at once."""
	monkeypatch.setattr(impulse_module, 'HOOK_RETRY_DELAY_SECONDS', 0)


def _call(radius=ImpulseRadius.SELF, destroys=False, rev=Reversibility.REVERSIBLE, count=1, always=False):
	return CallClassification(radius, destroys, rev, count, always)


def test_read_only_marks_reversible_and_zero():
	assert asyncio.run(classify_reversibility(read_note, {'path': 'x'})) == Reversibility.REVERSIBLE
	assert asyncio.run(classify_item_count(read_note, {'path': 'x'}, 11)) == 0


def test_count_hook_is_capped_at_limit():
	assert asyncio.run(classify_item_count(trash_folder, {'path': 'x'}, 11)) == 11


def test_missing_and_failing_hooks_fall_back_to_worst_case():
	for t in (flaky, undecorated):
		assert asyncio.run(classify_reversibility(t, {'path': 'x'})) == Reversibility.IRREVERSIBLE
		assert asyncio.run(classify_item_count(t, {'path': 'x'}, 11)) is None


def test_a_hook_the_server_throttles_once_is_retried(no_retry_pause):
	assert asyncio.run(classify_tool_call(write_private_note, {'path': 'x'})) == ImpulseRadius.SELF
	assert _radius_once.calls == 2
	assert asyncio.run(classify_destructive(write_private_note, {'path': 'x'})) is False
	assert _destroys_once.calls == 2


def test_a_hook_that_fails_twice_falls_back_to_external(no_retry_pause):
	_overloaded_calls.clear()
	assert asyncio.run(classify_tool_call(unreachable, {'path': 'x'})) == ImpulseRadius.EXTERNAL
	assert len(_overloaded_calls) == 2


def test_a_hook_that_cannot_answer_is_not_retried(no_retry_pause):
	_unanswerable_calls.clear()
	assert asyncio.run(classify_tool_call(unanswerable, {'path': 'x'})) == ImpulseRadius.EXTERNAL
	assert len(_unanswerable_calls) == 1


def test_only_unreachable_throttled_or_overloaded_counts_as_transient():
	from niquests import ConnectionError, Timeout
	assert impulse_module._is_transient(ConnectionError())
	assert impulse_module._is_transient(Timeout())
	assert impulse_module._is_transient(NextcloudException(429))
	assert impulse_module._is_transient(NextcloudException(502))
	assert not impulse_module._is_transient(NextcloudException(403))
	assert not impulse_module._is_transient(NextcloudException(404))
	assert not impulse_module._is_transient(NextcloudException())
	assert not impulse_module._is_transient(ValueError('HTTP 429'))


def test_small_reversible_self_batch_runs():
	policy = ConfirmationPolicy()
	assert not policy.needs_confirmation([_call(), _call(count=0)])


def test_irreversible_gate():
	policy = ConfirmationPolicy(irreversible_threshold=ImpulseRadius.INDIVIDUALS)
	# irreversible but only reaching the user: below the bar
	assert not policy.needs_confirmation([_call(rev=Reversibility.IRREVERSIBLE)])
	policy = ConfirmationPolicy(impulse_threshold=ImpulseRadius.EXTERNAL, irreversible_threshold=ImpulseRadius.GROUP)
	assert policy.needs_confirmation([_call(radius=ImpulseRadius.GROUP, rev=Reversibility.IRREVERSIBLE)])
	assert not policy.needs_confirmation([_call(radius=ImpulseRadius.GROUP, rev=Reversibility.RECOVERABLE)])


def test_irreversible_from_recoverable():
	policy = ConfirmationPolicy(
		irreversible_threshold=ImpulseRadius.SELF,
		irreversible_from=Reversibility.RECOVERABLE,
	)
	assert policy.needs_confirmation([_call(rev=Reversibility.RECOVERABLE)])
	assert not policy.needs_confirmation([_call(rev=Reversibility.REVERSIBLE)])


def test_irreversible_gate_looks_at_the_irreversible_call_only():
	# a reversible group call next to an irreversible private one is not an
	# irreversible group call
	policy = ConfirmationPolicy(impulse_threshold=ImpulseRadius.EXTERNAL, irreversible_threshold=ImpulseRadius.GROUP)
	assert not policy.needs_confirmation([
		_call(radius=ImpulseRadius.GROUP),
		_call(rev=Reversibility.IRREVERSIBLE),
	])


def test_relaxing_reversible_deletions():
	deletion = _call(destroys=True, rev=Reversibility.REVERSIBLE)
	assert ConfirmationPolicy().needs_confirmation([deletion])
	relaxed = ConfirmationPolicy(relax_reversible_deletions=True)
	assert not relaxed.needs_confirmation([deletion])
	# trash still counts as losing something
	assert relaxed.needs_confirmation([_call(destroys=True, rev=Reversibility.RECOVERABLE)])


def test_item_count_is_summed_over_the_batch():
	policy = ConfirmationPolicy(item_count_threshold=10)
	assert not policy.needs_confirmation([_call(count=5), _call(count=5)])
	assert policy.needs_confirmation([_call(count=5), _call(count=6)])


def test_unknown_item_count_is_confirmed():
	assert ConfirmationPolicy().needs_confirmation([_call(count=None)])


def test_always_confirm_wins():
	policy = ConfirmationPolicy(
		impulse_threshold=ImpulseRadius.EXTERNAL,
		destructive_threshold=ImpulseRadius.EXTERNAL,
		irreversible_threshold=ImpulseRadius.EXTERNAL,
		item_count_threshold=1000,
	)
	assert policy.needs_confirmation([_call(always=True)])


def test_always_allowed_calls_are_left_out():
	# even the strictest policy lets a vouched-for call through ...
	policy = ConfirmationPolicy(impulse_threshold=ImpulseRadius.SELF, item_count_threshold=0)
	assert not policy.needs_confirmation([ALLOWED_CALL])
	# ... and judges the rest of the batch without it
	assert policy.needs_confirmation([ALLOWED_CALL, _call()])
	assert not ConfirmationPolicy().needs_confirmation([ALLOWED_CALL, _call(count=10)])


def test_always_allow_marks_the_tool():
	@tool
	async def mcp_like(path: str):
		"""Like an MCP tool."""

	assert not is_always_allowed(mcp_like)
	always_allow(mcp_like.coroutine)
	assert is_always_allowed(mcp_like)
	assert not is_always_allowed(undecorated)


def _annotated(annotations):
	@tool
	async def mcp_like(path: str):
		"""Like an MCP tool."""

	from_mcp_annotations(mcp_like.coroutine, annotations)
	args = {'path': 'x'}
	return (
		asyncio.run(classify_tool_call(mcp_like, args)),
		asyncio.run(classify_destructive(mcp_like, args)),
		asyncio.run(classify_reversibility(mcp_like, args)),
		asyncio.run(classify_item_count(mcp_like, args, 11)),
	)


def test_read_only_annotation_runs_without_confirmation():
	radius, destroys, rev, count = _annotated({'readOnlyHint': True})
	assert (radius, destroys, rev, count) == (ImpulseRadius.SELF, False, Reversibility.REVERSIBLE, 0)
	assert not ConfirmationPolicy().needs_confirmation([CallClassification(radius, destroys, rev, count)])


def test_missing_annotations_take_the_worst_case_defaults():
	for annotations in (None, {}, {'readOnlyHint': False}):
		assert _annotated(annotations) == (ImpulseRadius.EXTERNAL, True, Reversibility.IRREVERSIBLE, None)


def test_closed_world_additive_annotation():
	assert _annotated({'destructiveHint': False, 'openWorldHint': False}) == (
		ImpulseRadius.GROUP, False, Reversibility.RECOVERABLE, None,
	)
	# adding something to the open world, like sending a message, cannot be taken back
	assert _annotated({'destructiveHint': False}) == (ImpulseRadius.EXTERNAL, False, Reversibility.IRREVERSIBLE, None)


def test_split_trusted_servers():
	config = {
		'weather': {'url': 'https://w/mcp', 'transport': 'streamable_http', 'trust_annotations': True},
		'other': {'url': 'https://o/mcp', 'transport': 'streamable_http', 'trust_annotations': 'yes'},
		'plain': {'url': 'https://p/mcp', 'transport': 'streamable_http'},
	}
	cleaned, trusted = split_trusted_servers(config)
	assert trusted == {'weather'}
	assert all('trust_annotations' not in c for c in cleaned.values())
	# the admin's config is left as it was
	assert config['weather']['trust_annotations'] is True


def test_parse_always_allow():
	assert parse_always_allow('get_forecast, mcp_get_alerts\nother') == {'get_forecast', 'get_alerts', 'other'}
	assert parse_always_allow('*') == {'*'}
	assert parse_always_allow('') == set()
	assert parse_always_allow(None) == set()


if __name__ == '__main__':
	for name, fn in list(globals().items()):
		if name.startswith('test_') and callable(fn):
			fn()
			print(f'ok {name}')
