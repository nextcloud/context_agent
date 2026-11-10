# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Classification hooks and the ConfirmationPolicy that judges them.

Runnable standalone (``python tests/test_confirmation_policy.py``) or via pytest.
No network: tools are plain LangChain tools with constant or local hooks.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool  # noqa: E402

from ex_app.lib.all_tools.lib.impulse import (  # noqa: E402
	ALLOWED_CALL,
	CallClassification,
	ConfirmationPolicy,
	ImpulseRadius,
	Reversibility,
	always_allow,
	classify_item_count,
	classify_reversibility,
	destructive,
	impulse,
	is_always_allowed,
	item_count,
	read_only,
	reversibility,
)
from ex_app.lib.all_tools.mcp import parse_always_allow  # noqa: E402


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
