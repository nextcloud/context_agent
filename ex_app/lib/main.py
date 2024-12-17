import os
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
from provider import provider, task_type


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
    print(f"enabled={enabled}")
    if enabled:
        nc.providers.task_processing.register(provider, task_type)
        app_enabled.set()
        nc.log(LogLvl.WARNING, f"App enabled: {nc.app_cfg.app_name}")
    else:
        nc.providers.task_processing.unregister(provider.id)
        app_enabled.clear()
        nc.log(LogLvl.WARNING, f"App disabled: {nc.app_cfg.app_name}")
    # In case of an error, a non-empty short string should be returned, which will be shown to the NC administrator.
    return ""


def background_thread_task():
    nc = NextcloudApp()

    while True:
        if not app_enabled.is_set():
            sleep(5)
            continue

        try:
            response = nc.providers.task_processing.next_task([provider.id], [task_type.id])
            if not response or not 'task' in response:
                sleep(2)
                continue
        except (NextcloudException, httpx.RequestError, JSONDecodeError) as e:
            print("Error fetching the next task", e, flush=True)
            sleep(5)
            continue

        task = response["task"]
        print(task, flush=True)

        try:
            output = react(task, nc)
        except Exception as e:  # noqa
            tb_str = ''.join(traceback.format_exception(e))
            print("Error:", tb_str, flush=True)
            try:
                nc = NextcloudApp()
                nc.log(LogLvl.ERROR, tb_str)
                nc.providers.task_processing.report_result(task["id"], error_message=str(e))
            except (NextcloudException, httpx.RequestError) as net_err:
                print("Network error in reporting the error:", net_err, flush=True)

            sleep(5)
        try:
            NextcloudApp().providers.task_processing.report_result(
                task["id"],
                output,
            )
        except (NextcloudException, httpx.RequestError, JSONDecodeError) as e:
            print("Network trying to report the task result:", e, flush=True)
            sleep(5)



def start_bg_task():
    t = Thread(target=background_thread_task, args=())
    t.start()

if __name__ == "__main__":
    # Wrapper around `uvicorn.run`.
    # You are free to call it directly, with just using the `APP_HOST` and `APP_PORT` variables from the environment.
    run_app("main:APP", log_level="trace")
