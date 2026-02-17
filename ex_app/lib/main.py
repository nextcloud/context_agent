# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import concurrent.futures
import os
import threading
import traceback
from contextlib import asynccontextmanager
from json import JSONDecodeError
from threading import Event
import asyncio

from niquests import RequestException
import json
from fastapi import FastAPI
from nc_py_api import NextcloudApp, NextcloudException, AsyncNextcloudApp
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
from ex_app.lib.mcp_server import UserAuthMiddleware, ToolListMiddleware
from ex_app.lib.provider import provider
from ex_app.lib.tools import get_categories

from contextvars import ContextVar
from gettext import translation
from fastmcp import FastMCP

mcp = FastMCP(name="nextcloud")
mcp.add_middleware(UserAuthMiddleware())
mcp.add_middleware(ToolListMiddleware(mcp))
mcp.stateless_http = True
http_mcp_app = mcp.http_app("/", transport="http")

fast_app = FastAPI(lifespan=http_mcp_app.lifespan)

app_enabled = Event()
TRIGGER = asyncio.Event()
IDLE_POLLING_INTERVAL = 5
IDLE_POLLING_INTERVAL_WITH_TRIGGER = 5 * 60

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
    set_handlers(
        app,
        enabled_handler,
        trigger_handler=trigger_handler,
    )
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


async def enabled_handler(enabled: bool, nc: AsyncNextcloudApp) -> str:
    # This will be called each time application is `enabled` or `disabled`
    # NOTE: `user` is unavailable on this step, so all NC API calls that require it will fail as unauthorized.
    await log(nc, LogLvl.INFO, f"enabled={enabled}")
    if enabled:
        await nc.providers.task_processing.register(provider)
        app_enabled.set()
        await log(nc, LogLvl.WARNING, f"App enabled: {nc.app_cfg.app_name}")

        await nc.ui.settings.register_form(SETTINGS)
        pref_settings = json.loads(await nc.appconfig_ex.get_value('tool_status', default = "{}"))
        for key in categories.keys(): # populate new settings values
            if key not in pref_settings:
                pref_settings[key] = True
        await nc.appconfig_ex.set_value('tool_status', json.dumps(pref_settings))

    else:
        await nc.providers.task_processing.unregister(provider.id)
        app_enabled.clear()
        await log(nc, LogLvl.WARNING, f"App disabled: {nc.app_cfg.app_name}")
    # In case of an error, a non-empty short string should be returned, which will be shown to the NC administrator.
    return ""


async def background_thread_task():
    nc = AsyncNextcloudApp()

    async with asyncio.TaskGroup() as tg:
        while True:
            if not app_enabled.is_set():
                await asyncio.sleep(5)
                continue

            try:
                response = await nc.providers.task_processing.next_task([provider.id], [provider.task_type])
                if not response or not 'task' in response:
                    if NUM_RUNNING_TASKS == 0:
                        # if there are no running tasks we will get a trigger
                        await wait_for_task()
                    else:
                        # otherwise, wait with fast frequency
                        await asyncio.sleep(2)
                    continue
            except (NextcloudException, RequestException, JSONDecodeError) as e:
                tb_str = ''.join(traceback.format_exception(e))
                await log(nc, LogLvl.WARNING, "Error fetching the next task " + tb_str)
                await wait_for_task(5)
                continue

            task = response["task"]
            await log(nc, LogLvl.INFO, 'New Task incoming')
            await log(nc, LogLvl.DEBUG, str(task))
            await log(nc, LogLvl.INFO, str({'input': task['input']['input'], 'confirmation': task['input']['confirmation'], 'conversation_token': '<skipped>', 'memories': task['input'].get('memories', None)}))
            tg.create_task(handle_task(task, nc))

NUM_RUNNING_TASKS_LOCK = asyncio.Lock()
NUM_RUNNING_TASKS = 0

async def handle_task(task, nc: AsyncNextcloudApp):
    global NUM_RUNNING_TASKS
    try:
        async with NUM_RUNNING_TASKS_LOCK:
            NUM_RUNNING_TASKS += 1
        nextcloud = AsyncNextcloudApp()
        if task['userId']:
            await nextcloud.set_user(task['userId'])
        output = await react(task, nextcloud)
    except Exception as e:  # noqa
        try:
            tb_str = ''.join(traceback.format_exception(e))
            await log(nc, LogLvl.ERROR, "Error: " + tb_str)
            await nc.providers.task_processing.report_result(task["id"], error_message=str(e))
        except (NextcloudException, RequestException) as net_err:
            tb_str = ''.join(traceback.format_exception(net_err))
            await log(nc, LogLvl.WARNING, "Network error in reporting the error: " + tb_str)
        finally:
            async with NUM_RUNNING_TASKS_LOCK:
                NUM_RUNNING_TASKS -= 1
        return
    try:
        await nc.providers.task_processing.report_result(
            task["id"],
            output,
        )
    except (NextcloudException, RequestException, JSONDecodeError) as e:
        tb_str = ''.join(traceback.format_exception(e))
        await log(nc, LogLvl.ERROR, "Network error trying to report the task result: " + tb_str)
    finally:
        async with NUM_RUNNING_TASKS_LOCK:
            NUM_RUNNING_TASKS -= 1



def start_bg_task():
    loop = asyncio.get_event_loop()
    loop.create_task(background_thread_task())

# Trigger event is available starting with nextcloud v33
async def trigger_handler(providerId: str):
    # now runs in the same thread as the task processing, which is why we can use asyncio.Event
    global TRIGGER
    TRIGGER.set()

# Waits for interval seconds or IDLE_POLLING_INTERVAL seconds
# but can return earlier when TRIGGER event is received from nextcloud
# if the trigger event is received, IDLE_POLLING_INTERVAL is set to IDLE_POLLING_INTERVAL_WITH_TRIGGER
async def wait_for_task(interval = None):
    global TRIGGER
    global IDLE_POLLING_INTERVAL
    global IDLE_POLLING_INTERVAL_WITH_TRIGGER
    if interval is None:
        interval = IDLE_POLLING_INTERVAL
    try:
        await asyncio.wait_for(TRIGGER.wait(), timeout=interval)
        # In case we received the event, we change the polling interval
        IDLE_POLLING_INTERVAL = IDLE_POLLING_INTERVAL_WITH_TRIGGER
    except asyncio.TimeoutError:
        pass
    TRIGGER.clear()


APP.mount("/mcp", http_mcp_app)

if __name__ == "__main__":
    # Wrapper around `uvicorn.run`.
    # You are free to call it directly, with just using the `APP_HOST` and `APP_PORT` variables from the environment.
    run_app("main:APP", log_level="trace")
