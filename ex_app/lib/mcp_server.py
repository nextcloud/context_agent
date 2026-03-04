# SPDX-FileCopyrightText: 2024 LangChain, Inc.
# SPDX-License-Identifier: MIT
import time
from functools import wraps

from fastmcp.server.dependencies import get_context
from nc_py_api import NextcloudApp
from nc_py_api._session import AsyncNcSessionApp, NcSessionApp
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools import Tool
from mcp import types as mt
from ex_app.lib.tools import get_tools
import requests


def _patch_disable_http3() -> None:
        original_async = AsyncNcSessionApp._create_adapter
        original_sync = NcSessionApp._create_adapter

        def _sync_wrapper(self, dav=False):
                session = original_sync(self, dav)
                session._disable_http3 = True
                return session

        def _async_wrapper(self, dav=False):
                session = original_async(self, dav)
                session._disable_http3 = True
                return session

        NcSessionApp._create_adapter = _sync_wrapper
        AsyncNcSessionApp._create_adapter = _async_wrapper


_patch_disable_http3()


def get_user(authorization_header: str, nc: NextcloudApp) -> str:
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
                nc = NextcloudApp()
                authorization_header = None
                try:
                        authorization_header = context.fastmcp_context.request_context.request.headers.get("Authorization")
                except Exception:
                        authorization_header = None

                if authorization_header:
                        try:
                                user = get_user(authorization_header, nc)
                                nc.set_user(user)
                        except Exception:
                                pass

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
                try:
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
                except Exception:
                        pass
                return await call_next(context)


def mcp_tool(tool):
        @wraps(tool)
        async def wrapper(*args, **kwargs):
                ctx = get_context()
                nc = ctx.get_state('nextcloud')
                safe, dangerous = await get_tools(nc)
                tools = safe + dangerous
                for t in tools:
                        if hasattr(t, "func") and t.func and t.name == tool.__name__:
                                return t.func(*args, **kwargs)
                raise RuntimeError("Tool not found")
        return wrapper
