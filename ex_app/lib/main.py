import traceback
from contextlib import asynccontextmanager
from json import JSONDecodeError
from threading import Thread, Event
from time import sleep

import httpx
from fastapi import FastAPI
from nc_py_api import NextcloudApp, NextcloudException
from nc_py_api.ex_app import AppAPIAuthMiddleware, LogLvl, run_app, set_handlers

from agent import react
from logger import log
from provider import provider


app_enabled = Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    set_handlers(app, enabled_handler)
    start_bg_task()
    nc = NextcloudApp()
    if nc.enabled_state:
        app_enabled.set()
    yield


APP = FastAPI(lifespan=lifespan)
APP.add_middleware(AppAPIAuthMiddleware)  # set global AppAPI authentication middleware


def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    # This will be called each time application is `enabled` or `disabled`
    # NOTE: `user` is unavailable on this step, so all NC API calls that require it will fail as unauthorized.
    log(nc, LogLvl.INFO, f"enabled={enabled}")
    if enabled:
        nc.providers.task_processing.register(provider)
        app_enabled.set()
        log(nc, LogLvl.WARNING, f"App enabled: {nc.app_cfg.app_name}")
    else:
        nc.providers.task_processing.unregister(provider.id)
        app_enabled.clear()
        log(nc, LogLvl.WARNING, f"App disabled: {nc.app_cfg.app_name}")
    # In case of an error, a non-empty short string should be returned, which will be shown to the NC administrator.
    return ""


def background_thread_task():
    nc = NextcloudApp()

    while True:
        if not app_enabled.is_set():
            sleep(5)
            continue

        try:
            response = nc.providers.task_processing.next_task([provider.id], [provider.task_type])
            if not response or not 'task' in response:
                sleep(2)
                continue
        except (NextcloudException, httpx.RequestError, JSONDecodeError) as e:
            tb_str = ''.join(traceback.format_exception(e))
            log(nc, LogLvl.WARNING, "Error fetching the next task " + tb_str)
            sleep(5)
            continue

        task = response["task"]
        log(nc, LogLvl.INFO, 'New Task incoming')
        log(nc, LogLvl.DEBUG, str(task))
        log(nc, LogLvl.INFO, str({'input': task['input']['input'], 'confirmation': task['input']['confirmation'], 'conversation_token': '<skipped>'}))

        try:
            nextcloud = NextcloudApp()
            if task['userId']:
                nextcloud.set_user(task['userId'])
            output = react(task, nextcloud)
        except Exception as e:  # noqa
            tb_str = ''.join(traceback.format_exception(e))
            log(nc, LogLvl.ERROR,"Error: " + tb_str)
            try:
                nc.providers.task_processing.report_result(task["id"], error_message=str(e))
            except (NextcloudException, httpx.RequestError) as net_err:
                tb_str = ''.join(traceback.format_exception(net_err))
                log(nc, LogLvl.WARNING, "Network error in reporting the error: " + tb_str)
            sleep(5)
            continue
        try:
            NextcloudApp().providers.task_processing.report_result(
                task["id"],
                output,
            )
        except (NextcloudException, httpx.RequestError, JSONDecodeError) as e:
            tb_str = ''.join(traceback.format_exception(e))
            log(nc, LogLvl.ERROR,"Network error trying to report the task result: " + tb_str)
            sleep(5)



def start_bg_task():
    t = Thread(target=background_thread_task, args=())
    t.start()

if __name__ == "__main__":
    # Wrapper around `uvicorn.run`.
    # You are free to call it directly, with just using the `APP_HOST` and `APP_PORT` variables from the environment.
    run_app("main:APP", log_level="trace")
