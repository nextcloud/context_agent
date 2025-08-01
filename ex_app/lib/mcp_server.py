# SPDX-FileCopyrightText: 2024 LangChain, Inc.
# SPDX-License-Identifier: MIT
import time
from functools import wraps

from fastmcp.server.dependencies import get_context
from nc_py_api import NextcloudApp
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools import Tool
from mcp import types as mt
from ex_app.lib.tools import get_tools

class UserAuthMiddleware(Middleware):
	async def on_message(self, context: MiddlewareContext, call_next):
		# Middleware stores user info in context state
		user = context.fastmcp_context.request_context.request.headers.get("Authorization")
		if user is None:
			raise Exception("Authorization header is missing/invalid")
		if user.startswith("Bearer "):
			user = user[len("Bearer "):]
		nc = NextcloudApp()
		nc.set_user(user)
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
		if LAST_MCP_TOOL_UPDATE + 60 < time.time():
			safe, dangerous = await get_tools(context.fastmcp_context.get_state("nextcloud"))
			tools = await self.mcp.get_tools()
			if LAST_MCP_TOOL_UPDATE + 60 < time.time():
				for tool in tools.keys():
					self.mcp.remove_tool(tool)
				for tool in safe + dangerous:
					if not hasattr(tool, "func") or tool.func is None:
						continue
					self.mcp.tool()(mcp_tool(tool.func))
				LAST_MCP_TOOL_UPDATE = time.time()
		return await call_next(context)

# Regenerates the tools with the correct nc object
def mcp_tool(tool):
	@wraps(tool)
	async def wrapper(*args, **kwargs):
		ctx = get_context()
		nc = ctx.get_state('nextcloud')
		safe, dangerous = await get_tools(nc)
		for t in safe + dangerous:
			if hasattr(t, "func") and t.func and t.name == tool.__name__:
				return t.func(*args, **kwargs)
		raise RuntimeError("Tool not found")
	return wrapper