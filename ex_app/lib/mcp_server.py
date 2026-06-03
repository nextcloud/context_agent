# SPDX-FileCopyrightText: 2024 LangChain, Inc.
# SPDX-License-Identifier: MIT
import asyncio
import inspect
import time
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, cast

from fastmcp.server.dependencies import get_context
from nc_py_api import AsyncNextcloudApp
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools import Tool
from mcp import types as mt
from ex_app.lib.tools import get_tools
import requests

# ContextVar to propagate the Authorization header into FastMCP background tasks.
# asyncio.create_task() copies the current context snapshot at task-creation time,
# so a ContextVar set before the task is spawned is visible inside it — even after
# Starlette's request context has been torn down.
_mcp_auth_header: ContextVar[str | None] = ContextVar("_mcp_auth_header", default=None)


class MCPAuthHeaderMiddleware:
	"""Pure-ASGI middleware that captures the Authorization header from every
	HTTP request and stores it in a ContextVar.

	Must be added to the outer FastAPI app (APP) so it runs before FastMCP takes
	ownership of the request. Because asyncio copies the current context when
	spawning tasks, the ContextVar value is available inside FastMCP's background-
	task processing even after the original Starlette request context is gone.
	"""

	def __init__(self, app):
		self.app = app

	async def __call__(self, scope, receive, send):
		if scope["type"] == "http":
			headers = {k: v for k, v in scope.get("headers", [])}
			raw_auth = headers.get(b"authorization", b"")
			auth_value = raw_auth.decode("latin-1") if raw_auth else None
			token = _mcp_auth_header.set(auth_value)
			try:
				await self.app(scope, receive, send)
			finally:
				_mcp_auth_header.reset(token)
		else:
			await self.app(scope, receive, send)


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
		authorization_header = _mcp_auth_header.get()

		if not authorization_header:
			raise Exception("Authorization header is missing/invalid")

		nc = AsyncNextcloudApp()
		user = get_user(authorization_header, nc)
		await nc.set_user(user)
		context.fastmcp_context.set_state("nextcloud", nc)
		return await call_next(context)


LAST_MCP_TOOL_UPDATE = 0


class ToolListMiddleware(Middleware):
	def __init__(self, mcp):
		self.mcp = mcp

	async def on_message(
			self,
			context: MiddlewareContext[mt.ListToolsRequest],
			call_next: CallNext[mt.ListToolsRequest, list[Tool]],
	) -> list[Tool]:
		global LAST_MCP_TOOL_UPDATE
		nc = context.fastmcp_context.get_state("nextcloud")
		if nc is None:
			return await call_next(context)

		now = time.time()
		if LAST_MCP_TOOL_UPDATE + 60 < now:
			safe, dangerous = await get_tools(nc)
			tools = await self.mcp.get_tools()
			for tool in tools.keys():
				self.mcp.remove_tool(tool)
			for tool in safe + dangerous:
				tool_action = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
				if tool_action is None:
					continue
				tool_name = getattr(tool, "name", None)
				if tool_name:
					self.mcp.tool(name=tool_name)(mcp_tool(tool_action, tool_name=tool_name))
				else:
					self.mcp.tool()(mcp_tool(tool_action))
			LAST_MCP_TOOL_UPDATE = now
		return await call_next(context)

# Regenerates the tools with the correct nc object
def mcp_tool(tool, tool_name: str | None = None):
	@wraps(tool)
	async def wrapper(*args, **kwargs):
		ctx = get_context()
		nc = ctx.get_state('nextcloud')
		safe, dangerous = await get_tools(nc)
		tools = safe + dangerous
		invoked_name = tool_name or tool.__name__
		for t in tools:
			action = getattr(t, "coroutine", None) or getattr(t, "func", None)
			if action is None:
				continue
			action = cast(Callable[..., Any], action)
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