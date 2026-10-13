
# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import traceback
from contextlib import asynccontextmanager
from threading import Event
import asyncio
import logging
import time

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
from ex_app.lib.errors import UserFacingError
from ex_app.lib.logger import log
from ex_app.lib.mcp_server import UserAuthMiddleware, ToolListMiddleware
from ex_app.lib.provider import provider, multimodal_provider
from ex_app.lib.tools import get_categories

PROVIDERS = [provider, multimodal_provider]
PROVIDER_IDS = [p.id for p in PROVIDERS]
TASK_TYPES = [p.task_type for p in PROVIDERS]

from contextvars import ContextVar
from gettext import translation
from fastmcp import FastMCP

mcp = FastMCP(name="nextcloud")
mcp.add_middleware(UserAuthMiddleware())
mcp.add_middleware(ToolListMiddleware(mcp))
http_mcp_app = mcp.http_app("/", transport="http", stateless_http=True)


MCP_METHOD_NOT_ALLOWED = json.dumps({
    "jsonrpc": "2.0",
    "id": "server-error",
    "error": {"code": -32600, "message": "Method Not Allowed: this server does not offer an SSE stream"},
}).encode()


class MCPTransportMiddleware:
    """Smooth over two rough edges of the mounted MCP app.

    1. The MCP app is mounted at /mcp and serves "/", so Starlette answers a bare
       /mcp with a 307 whose Location is rebuilt from the forwarded Host, dropping
       the AppAPI proxy prefix. MCP clients follow redirects, land on Nextcloud
       itself and get a 404, which the MCP SDK surfaces as "Session terminated".
       Serve /mcp directly instead of redirecting to /mcp/.
    2. We run the MCP app stateless, so a standalone GET stream can never carry
       anything: every request gets its own transport and server-initiated
       messages go out over that request's own SSE stream. Left to the SDK the
       GET opens a stream that never emits and never closes, pinning a proxy
       connection per client. Answer 405 instead, which clients handle as
       "no SSE stream offered here".
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in ("/mcp", "/mcp/"):
            if scope["method"] == "GET":
                await send({
                    "type": "http.response.start",
                    "status": 405,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(MCP_METHOD_NOT_ALLOWED)).encode()),
                        (b"allow", b"POST, DELETE"),
                    ],
                })
                await send({"type": "http.response.body", "body": MCP_METHOD_NOT_ALLOWED})
                return
            if scope["path"] == "/mcp":
                scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)


fast_app = FastAPI(lifespan=http_mcp_app.lifespan)

app_enabled = Event()
TRIGGER = asyncio.Event()
logger = logging.getLogger("uvicorn.error")


def positive_timeout(name: str, default: int) -> int:
    """Read a positive timeout from the environment without breaking startup."""
    try:
        value = int(os.getenv(name, str(default)))
        if value > 0:
            return value
    except ValueError:
        pass
    logger.warning("Invalid %s; using default %s seconds", name, default)
    return default


CLAIM_TIMEOUT_SECONDS = positive_timeout("CONTEXT_AGENT_CLAIM_TIMEOUT_SEC", 60)
IDLE_POLLING_INTERVAL = positive_timeout("CONTEXT_AGENT_IDLE_POLL_SEC", 30)
POLL_RETRY_INTERVAL = 5
BUSY_POLL_INTERVAL = 2


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
    nc = NextcloudApp()
    if nc.enabled_state:
        app_enabled.set()

    poller = asyncio.create_task(supervise_poller(), name="context-agent-poller")
    try:
        yield
    finally:
        poller.cancel()
        try:
            await poller
        except asyncio.CancelledError:
            pass


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
        for p in PROVIDERS:
            await nc.providers.task_processing.register(p)
        app_enabled.set()
        await log(nc, LogLvl.WARNING, f"App enabled: {nc.app_cfg.app_name}")

        await nc.ui.settings.register_form(SETTINGS)
        pref_settings = json.loads(await nc.appconfig_ex.get_value('tool_status', default = "{}"))
        for key in categories.keys(): # populate new settings values
            if key not in pref_settings:
                pref_settings[key] = True
        await nc.appconfig_ex.set_value('tool_status', json.dumps(pref_settings))

    else:
        for p in PROVIDERS:
            await nc.providers.task_processing.unregister(p.id)
        app_enabled.clear()
        await log(nc, LogLvl.WARNING, f"App disabled: {nc.app_cfg.app_name}")
    # In case of an error, a non-empty short string should be returned, which will be shown to the NC administrator.
    return ""


async def supervise_poller():
    """Keep task acquisition alive even if its coroutine exits unexpectedly."""
    while True:
        try:
            logger.info("Task poller starting")
            await background_thread_task()
            logger.error("Task poller stopped unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Task poller crashed; restarting")
        await asyncio.sleep(POLL_RETRY_INTERVAL)


async def handle_task_safely(task, nc: AsyncNextcloudApp):
    """Do not let an unexpected handler failure cancel sibling tasks."""
    task_id = task.get("id") if isinstance(task, dict) else None
    started = time.monotonic()
    try:
        await handle_task(task, nc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Unhandled error in Context Agent task %s", task_id)
        # handle_task() normally reports task failures itself; this fallback only
        # runs if unexpected code or error-reporting failures escape the handler.
        try:
            await asyncio.wait_for(report_error(nc, task_id, exc), timeout=20)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to report unexpected error for task %s", task_id)
    finally:
        logger.info("Task handler %s ended after %.2f s", task_id, time.monotonic() - started)


async def background_thread_task():
    nc = AsyncNextcloudApp()
    async with asyncio.TaskGroup() as tg:
        while True:
            if not app_enabled.is_set():
                await asyncio.sleep(POLL_RETRY_INTERVAL)
                continue

            started = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    nc.providers.task_processing.next_task(PROVIDER_IDS, TASK_TYPES),
                    timeout=CLAIM_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to fetch next Context Agent task; retrying")
                await wait_for_task(POLL_RETRY_INTERVAL)
                continue

            if not response or "task" not in response:
                async with NUM_RUNNING_TASKS_LOCK:
                    no_tasks_running = NUM_RUNNING_TASKS == 0
                if no_tasks_running:
                    await wait_for_task()
                else:
                    await asyncio.sleep(BUSY_POLL_INTERVAL)
                continue

            task = response["task"]
            logger.info(
                "Claimed Context Agent task %s in %.2f s",
                task.get("id"), time.monotonic() - started,
            )
            tg.create_task(handle_task_safely(task, nc))


NUM_RUNNING_TASKS_LOCK = asyncio.Lock()
NUM_RUNNING_TASKS = 0

async def report_error(nc: AsyncNextcloudApp, task_id: int, e: Exception):
    """Report a failed task, passing on the user-facing error message when we have one."""
    # The user-facing message is only picked up by Nextcloud 33+, older versions ignore it.
    await nc.providers.task_processing.report_result(
        task_id,
        error_message=str(e),
        user_facing_error_message=e.user_facing_message if isinstance(e, UserFacingError) else None,
    )


async def handle_task(task, nc: AsyncNextcloudApp):
    global NUM_RUNNING_TASKS
    try:
        async with NUM_RUNNING_TASKS_LOCK:
            NUM_RUNNING_TASKS += 1
        nextcloud = AsyncNextcloudApp()
        if task['userId']:
            await nextcloud.set_user(task['userId'])

        stream_updates_enabled = task.get('preferStreaming', None) is True
        stream_update_failed = False

        async def stream_output(intermediate_output):
            nonlocal stream_update_failed
            if not stream_updates_enabled or stream_update_failed:
                return
            try:
                await nc.ocs(
                    "POST",
                    f"/ocs/v2.php/taskprocessing/tasks_provider/{task['id']}/stream-result",
                    json={"output": intermediate_output},
                )
            except (NextcloudException, RequestException) as stream_err:
                stream_update_failed = True
                tb_str = ''.join(traceback.format_exception(stream_err))
                await log(nc, LogLvl.WARNING, "Error streaming intermediate task result: " + tb_str)

        output = await react(task, nextcloud, stream_output=stream_output if stream_updates_enabled else None)
    except Exception as e:  # noqa
        try:
            tb_str = ''.join(traceback.format_exception(e))
            await log(nc, LogLvl.ERROR, "Error: " + tb_str)
            await report_error(nc, task["id"], e)
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
    except Exception as e:
        try:
            tb_str = ''.join(traceback.format_exception(e))
            await log(nc, LogLvl.ERROR, "Error trying to report the task result: " + tb_str)
        except Exception:
            pass
    finally:
        async with NUM_RUNNING_TASKS_LOCK:
            NUM_RUNNING_TASKS -= 1



# Trigger event is available starting with Nextcloud v33.
async def trigger_handler(providerId: str):
    TRIGGER.set()


async def wait_for_task(interval: int | None = None):
    """Wait for a trigger, falling back to periodic task acquisition."""
    if interval is None:
        interval = IDLE_POLLING_INTERVAL
    try:
        await asyncio.wait_for(TRIGGER.wait(), timeout=interval)
    except asyncio.TimeoutError:
        pass
    TRIGGER.clear()


APP.mount("/mcp", http_mcp_app)
APP.add_middleware(MCPTransportMiddleware)

if __name__ == "__main__":
    # Wrapper around uvicorn.run.
    # You are free to call it directly, with just using APP_HOST and APP_PORT.
    run_app("main:APP", log_level="trace")
