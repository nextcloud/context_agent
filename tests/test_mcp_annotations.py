# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hints the MCP server hands out, derived from the classification markers.

Runnable standalone (``python tests/test_mcp_annotations.py``) or via pytest.
No network: the tools are registered on a local FastMCP server and listed back.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import Client, FastMCP  # noqa: E402
from langchain_core.tools import StructuredTool, tool  # noqa: E402

from ex_app.lib.all_tools.lib.impulse import (  # noqa: E402
	ImpulseRadius,
	Reversibility,
	always_confirm,
	destructive,
	destructive_if,
	impulse,
	item_count,
	read_only,
	reversibility,
)
from ex_app.lib.mcp_server import IMPULSE_META_KEY, register_tool  # noqa: E402


@tool
@impulse(ImpulseRadius.SELF)
@read_only
async def read_note(path: str):
	"""Read a note."""


async def _share_radius(group=False):
	return ImpulseRadius.GROUP if group else ImpulseRadius.INDIVIDUALS


@tool
@impulse(_share_radius)
@reversibility(Reversibility.IRREVERSIBLE)
@item_count(1)
async def share_note(path: str, group: bool = False):
	"""Share a note."""


@tool
@impulse(ImpulseRadius.SELF)
@destructive
@reversibility(Reversibility.RECOVERABLE)
@item_count(1)
async def trash_note(path: str):
	"""Trash a note."""


@tool
@impulse(ImpulseRadius.SELF)
@destructive_if(lambda path=None: True)
@reversibility(lambda path=None: Reversibility.RECOVERABLE)
@item_count(1)
async def write_note(path: str):
	"""Write a note."""


@tool
@impulse(ImpulseRadius.SELF)
@always_confirm
@read_only
async def schedule_reading(path: str):
	"""Reads now, but hands the agent a prompt to run later."""


@tool
async def unmarked(path: str):
	"""Carries no markers, like an MCP tool."""


async def dynamic_action(**tool_args):
	return tool_args['query']


dynamic_tool = StructuredTool(
	name='dynamic_search',
	description='Search a dynamically discovered app.',
	args_schema={
		'type': 'object',
		'properties': {'query': {'type': 'string'}},
		'required': ['query'],
	},
	coroutine=dynamic_action,
)


async def _list_tools():
	server = FastMCP(name="test")
	for t in (read_note, share_note, trash_note, write_note, schedule_reading, unmarked, dynamic_tool):
		register_tool(server, t)
	async with Client(server) as client:
		return {t.name: t for t in await client.list_tools()}


def test_hints():
	tools = asyncio.run(_list_tools())

	assert tools['read_note'].annotations.readOnlyHint is True
	assert tools['read_note'].annotations.destructiveHint is False
	assert tools['read_note'].meta[IMPULSE_META_KEY] == {
		'radius': 'self', 'destructive': False, 'reversibility': 'reversible',
		'item_count': 0, 'always_confirm': False,
	}

	# shares disclose but destroy nothing
	assert tools['share_note'].annotations.readOnlyHint is False
	assert tools['share_note'].annotations.destructiveHint is False
	assert tools['share_note'].meta[IMPULSE_META_KEY]['radius'] == 'dynamic'
	assert tools['share_note'].meta[IMPULSE_META_KEY]['reversibility'] == 'irreversible'

	# destructive, or maybe destructive: the hint is left to the spec's default (true)
	for name in ('trash_note', 'write_note'):
		assert tools[name].annotations.readOnlyHint is False
		assert tools[name].annotations.destructiveHint is None
	assert tools['write_note'].meta[IMPULSE_META_KEY]['destructive'] == 'dynamic'

	# confirmed whatever it does, so not offered to clients as safe to run
	assert tools['schedule_reading'].annotations.readOnlyHint is False
	assert tools['schedule_reading'].annotations.destructiveHint is False
	assert tools['schedule_reading'].meta[IMPULSE_META_KEY]['always_confirm'] is True

	# no markers: the worst case, destructive included
	assert tools['unmarked'].annotations.readOnlyHint is False
	assert tools['unmarked'].annotations.destructiveHint is None
	assert tools['unmarked'].meta[IMPULSE_META_KEY] == {
		'radius': 'external', 'destructive': True, 'reversibility': 'irreversible',
		'item_count': 'dynamic', 'always_confirm': False,
	}

	# Dynamically discovered OpenAPI tools declare a schema on StructuredTool
	# while their callable accepts **kwargs. FastMCP must use the declared schema.
	assert tools['dynamic_search'].inputSchema == {
		'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query'],
	}


if __name__ == '__main__':
	test_hints()
	print('ok')
