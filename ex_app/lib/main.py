# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import time
import traceback
from contextlib import asynccontextmanager
from json import JSONDecodeError
from threading import Event
import asyncio

import httpx
import json
from fastapi import FastAPI
from fastmcp.tools import Tool
from mcp import types as mt
from nc_py_api import NextcloudApp, NextcloudException
from nc_py_api.ex_app import (
    AppAPIAuthMiddleware,
    LogLvl,
    run_app,
    set_handlers,
    SettingsForm,
    SettingsField,
    SettingsFieldType)

from ex_app.lib.agent import react
from ex_app.lib.logger import log
from ex_app.lib.provider import provider
from ex_app.lib.tools import get_categories, get_tools

from contextvars import ContextVar
from gettext import translation
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext

class UserAuthMiddleware(Middleware):
    async def on_message(self, context: MiddlewareContext, call_next):
        # Middleware stores user info in context state
        user = context.fastmcp_context.request_context.request.headers.get("Authorization")
        if user.startswith("Bearer "):
            user = user[len("Bearer "):]
        nc = NextcloudApp()
        nc.set_user(user)
        context.fastmcp_context.set_state("nextcloud", nc)
        return await call_next(context)

LAST_MCP_TOOL_UPDATE = 0
class ToolListMiddleware(Middleware):
    async def on_message(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, list[Tool]],
    ) -> list[Tool]:
        global LAST_MCP_TOOL_UPDATE
        if LAST_MCP_TOOL_UPDATE + 60 < time.time():
            safe, dangerous = await get_tools(context.fastmcp_context.get_state("nextcloud"))
            tools = await mcp.get_tools()
            if LAST_MCP_TOOL_UPDATE + 60 < time.time():
                for tool in tools.keys():
                    mcp.remove_tool(tool)
                for tool in safe + dangerous:
                    mcp.tool()(tool.func)
                LAST_MCP_TOOL_UPDATE = time.time()
        return await call_next(context)


mcp = FastMCP(name="nextcloud")
mcp.add_middleware(UserAuthMiddleware())
mcp.add_middleware(ToolListMiddleware())
mcp.stateless_http = True
http_mcp_app = mcp.http_app("/", transport="http")

fast_app = FastAPI(lifespan=http_mcp_app.lifespan)

app_enabled = Event()

LOCALE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locale")
current_translator = ContextVar("current_translator")
current_translator.set(translation(os.getenv("APP_ID"), LOCALE_DIR, languages=["en"], fallback=True))

def _(text):
    return current_translator.get().gettext(text)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with exapp_lifespan(app):
        async with http_mcp_app.lifespan(app):
            yield
@asynccontextmanager
async def exapp_lifespan(app: FastAPI):
    set_handlers(app, enabled_handler)
    start_bg_task()
    nc = NextcloudApp()
    if nc.enabled_state:
        app_enabled.set()
    yield


APP = FastAPI(lifespan=lifespan)
APP.add_middleware(AppAPIAuthMiddleware)  # set global AppAPI authentication middleware
categories=get_categories()

SETTINGS = SettingsForm(
    id="settings_context_agent",
    section_type="admin",
    section_id="ai",
    title=_("Context Agent"),
    description=_("Find more details on how to set up Context Agent in the Administration documentation."),
    fields=[
        SettingsField(
            id="tool_status",
            title=_("Activate all tools that Context Agent should use"),
            type=SettingsFieldType.MULTI_CHECKBOX,
            default=dict.fromkeys(categories, True),
            options={v: k for k, v in categories.items()},
        ),
        SettingsField(
            id="here_api",
            title=_("API Key HERE"),
            description=_("Set the API key for the HERE public transport routing"),
            type=SettingsFieldType.PASSWORD,
            default="",
            placeholder=_("API key"),
        ),
		SettingsField(
            id="mcp_config",
            title=_("MCP Config"),
            description=_("JSON configuration for the MCP. Structured as {\"service_name\": {\"url\": \"https://service.url\",\"transport\": \"streamable_http\"}}. For more details view the documentation for context_agent."),
            type=SettingsFieldType.TEXT,
            default="",
            placeholder="{\"weather\": {\"url\": \"https://weather.internet/mcp\",\"transport\": \"streamable_http\"}}",
        ),
        ]
)


def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    # This will be called each time application is `enabled` or `disabled`
    # NOTE: `user` is unavailable on this step, so all NC API calls that require it will fail as unauthorized.
    log(nc, LogLvl.INFO, f"enabled={enabled}")
    if enabled:
        nc.providers.task_processing.register(provider)
        app_enabled.set()
        log(nc, LogLvl.WARNING, f"App enabled: {nc.app_cfg.app_name}")

        nc.ui.settings.register_form(SETTINGS)
        pref_settings = json.loads(nc.appconfig_ex.get_value('tool_status', default = "{}"))
        for key in categories.keys(): # populate new settings values
            if key not in pref_settings:
                pref_settings[key] = True
        nc.appconfig_ex.set_value('tool_status', json.dumps(pref_settings))

    else:
        nc.providers.task_processing.unregister(provider.id)
        app_enabled.clear()
        log(nc, LogLvl.WARNING, f"App disabled: {nc.app_cfg.app_name}")
    # In case of an error, a non-empty short string should be returned, which will be shown to the NC administrator.
    return ""


async def background_thread_task():
    nc = NextcloudApp()

    while True:
        if not app_enabled.is_set():
            await asyncio.sleep(5)
            continue

        try:
            response = nc.providers.task_processing.next_task([provider.id], [provider.task_type])
            if not response or not 'task' in response:
                await asyncio.sleep(2)
                continue
        except (NextcloudException, httpx.RequestError, JSONDecodeError) as e:
            tb_str = ''.join(traceback.format_exception(e))
            log(nc, LogLvl.WARNING, "Error fetching the next task " + tb_str)
            await asyncio.sleep(5)
            continue
        except (
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.LocalProtocolError,
                httpx.PoolTimeout,
        ) as e:
            log(nc, LogLvl.DEBUG, "Ignored error during task polling")
            await asyncio.sleep(2)
            continue

        task = response["task"]
        log(nc, LogLvl.INFO, 'New Task incoming')
        log(nc, LogLvl.DEBUG, str(task))
        log(nc, LogLvl.INFO, str({'input': task['input']['input'], 'confirmation': task['input']['confirmation'], 'conversation_token': '<skipped>'}))
        asyncio.create_task(handle_task(task, nc))
        await asyncio.sleep(5)


async def handle_task(task, nc: NextcloudApp):
    try:
        nextcloud = NextcloudApp()
        if task['userId']:
            nextcloud.set_user(task['userId'])
        output = await react(task, nextcloud)
    except Exception as e:  # noqa
        tb_str = ''.join(traceback.format_exception(e))
        log(nc, LogLvl.ERROR, "Error: " + tb_str)
        try:
            nc.providers.task_processing.report_result(task["id"], error_message=str(e))
        except (NextcloudException, httpx.RequestError) as net_err:
            tb_str = ''.join(traceback.format_exception(net_err))
            log(nc, LogLvl.WARNING, "Network error in reporting the error: " + tb_str)
        return
    try:
        NextcloudApp().providers.task_processing.report_result(
            task["id"],
            output,
        )
    except (NextcloudException, httpx.RequestError, JSONDecodeError) as e:
        tb_str = ''.join(traceback.format_exception(e))
        log(nc, LogLvl.ERROR, "Network error trying to report the task result: " + tb_str)



def start_bg_task():
    loop = asyncio.get_event_loop()
    loop.create_task(background_thread_task())

APP.mount("/mcp", http_mcp_app)

if __name__ == "__main__":
    # You are free to call it directly, with just using the `APP_HOST` and `APP_PORT` variables from the environment.
    run_app("main:APP", log_level="trace")
