# SPDX-FileCopyrightText: 2024 LangChain, Inc.
# SPDX-License-Identifier: MIT
import time
import asyncio
import inspect
from functools import wraps

from fastmcp.server.dependencies import get_context
from nc_py_api import AsyncNextcloudApp, NextcloudApp
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import Tool
from mcp import types as mt
from ex_app.lib.all_tools.lib.impulse import StaticClassification, static_classification
from ex_app.lib.tools import TOOL_MODULE_METADATA_KEY, get_tools
import requests

def get_user(authorization_header: str, nc: AsyncNextcloudApp) -> str:
	response = requests.get(
		f"{nc.app_cfg.endpoint}/ocs/v2.php/cloud/user",
		headers={
			"Accept": "application/json",
			"Ocs-Apirequest": "1",
			"Authorization": authorization_header,
		},
	)
	if response.status_code != 200:
		raise Exception("Failed to get user info")
	return response.json()["ocs"]["data"]["id"]


class UserAuthMiddleware(Middleware):
	async def on_message(self, context: MiddlewareContext, call_next):
		# Middleware stores user info in context state
		authorization_header = get_http_headers().get("authorization")
		if authorization_header is None:
			raise Exception("Authorization header is missing/invalid")
		nc = AsyncNextcloudApp()
		user = get_user(authorization_header, nc)
		await nc.set_user(user)
		context.fastmcp_context.set_state("nextcloud", nc)
		return await call_next(context)


LAST_MCP_TOOL_UPDATE = 0

# Modules in all_tools whose tools are not exposed on the MCP server. The tools of
# the configured MCP servers are only proxied by us; clients can talk to those
# servers directly, and we would end up serving ourselves if pointed at our own endpoint.
EXCLUDED_TOOL_MODULES = {"mcp"}


def is_exposed(tool) -> bool:
	return (getattr(tool, "metadata", None) or {}).get(TOOL_MODULE_METADATA_KEY) not in EXCLUDED_TOOL_MODULES


async def get_exposed_tools(nc):
	return [tool for tool in await get_tools(nc) if is_exposed(tool)]


class ToolListMiddleware(Middleware):
	def __init__(self, mcp):
		self.mcp = mcp

	async def on_message(
			self,
			context: MiddlewareContext[mt.ListToolsRequest],
			call_next: CallNext[mt.ListToolsRequest, list[Tool]],
	) -> list[Tool]:
		global LAST_MCP_TOOL_UPDATE
		if LAST_MCP_TOOL_UPDATE + 60 < time.time():
			nc_tools = await get_exposed_tools(context.fastmcp_context.get_state("nextcloud"))
			tools = await self.mcp.get_tools()
			if LAST_MCP_TOOL_UPDATE + 60 < time.time():
				for tool in tools.keys():
					self.mcp.remove_tool(tool)
				for tool in nc_tools:
					register_tool(self.mcp, tool)
				LAST_MCP_TOOL_UPDATE = time.time()
		return await call_next(context)


def register_tool(mcp, tool):
	"""Expose a LangChain tool on the MCP server, with the hints its markers allow."""
	tool_action = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
	if tool_action is None:
		return
	tool_name = getattr(tool, "name", None)
	classification = static_classification(tool)
	mcp.tool(
		name=tool_name or None,
		annotations=tool_annotations(classification),
		meta={IMPULSE_META_KEY: impulse_meta(classification)},
	)(mcp_tool(tool_action, tool_name=tool_name or None))

# Where the full classification goes in a tool's _meta, for clients that know
# what to make of it. The standard hints below can only carry part of it.
IMPULSE_META_KEY = "com.nextcloud/impulse"

# What a field of the classification is shown as when it depends on the arguments.
DYNAMIC = "dynamic"


def tool_annotations(classification: StaticClassification) -> mt.ToolAnnotations:
	"""The MCP hints a tool can honestly give before it is called.

	A hint is only set when it holds for every call; otherwise it is left out and
	the client assumes the worst, which is what the spec defaults to. The radius
	has no standard hint -- openWorldHint is about whether the tool talks to the
	outside world (a web search does), not who gains access to the user's data --
	so it only goes into the _meta.
	"""
	if classification.read_only:
		return mt.ToolAnnotations(readOnlyHint=True, destructiveHint=False)
	annotations = mt.ToolAnnotations(readOnlyHint=False)
	if classification.destroys is False:
		annotations.destructiveHint = False
	return annotations


def impulse_meta(classification: StaticClassification) -> dict:
	def show(value):
		if value is None:
			return DYNAMIC
		return value.name.lower() if hasattr(value, "name") else value

	return {
		"radius": show(classification.radius),
		"destructive": show(classification.destroys),
		"reversibility": show(classification.reversibility),
		"item_count": show(classification.item_count),
		"always_confirm": classification.always,
	}


# Regenerates the tools with the correct nc object
def mcp_tool(tool, tool_name: str | None = None):
	@wraps(tool)
	async def wrapper(*args, **kwargs):
		ctx = get_context()
		nc = ctx.get_state('nextcloud')
		tools = await get_exposed_tools(nc)
		invoked_name = tool_name or tool.__name__
		for t in tools:
			action = getattr(t, "coroutine", None) or getattr(t, "func", None)
			if action is None:
				continue
			candidate_name = getattr(t, "name", None) or getattr(action, "__name__", None)
			if candidate_name == invoked_name:
				if inspect.iscoroutinefunction(action):
					return await action(*args, **kwargs)
				result = await asyncio.to_thread(action, *args, **kwargs)
				if inspect.isawaitable(result):
					return await result
				return result
		raise RuntimeError("Tool not found")
	return wrapper