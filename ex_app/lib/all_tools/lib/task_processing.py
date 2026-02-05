# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import time
import typing

from niquests import ConnectionError, Timeout
from nc_py_api import AsyncNextcloudApp, NextcloudException
from nc_py_api.ex_app import LogLvl
from pydantic import BaseModel, ValidationError

from ex_app.lib.logger import log


class Task(BaseModel):
	id: int
	status: str
	output: dict[str, typing.Any] | None = None

class Response(BaseModel):
	task: Task

async def run_task(nc: AsyncNextcloudApp, type, task_input):
	i = 0
	while i < 20:
		try:
			response = await nc.ocs(
				"POST",
				"/ocs/v1.php/taskprocessing/schedule",
				json={"type": type, "appId": "context_agent", "input": task_input},
			)
			break
		except (
				ConnectionError,
				Timeout

		) as e:
			await log(nc, LogLvl.DEBUG, "Ignored error during task scheduling")
			i += 1
			await asyncio.sleep(1)
			continue

	if i >= 20:
		raise Exception("Failed to schedule task")

	try:
		task = Response.model_validate(response).task
		await log(nc, LogLvl.DEBUG, task)

		i = 0
		# wait for 5 seconds * 60 * 2 = 10 minutes (one i ^= 5 sec)
		while task.status != "STATUS_SUCCESSFUL" and task.status != "STATUS_FAILED" and i < 60 * 2:
			await asyncio.sleep(5)
			i += 1
			try:
				response = await nc.ocs("GET", f"/ocs/v1.php/taskprocessing/task/{task.id}")
			except (
					ConnectionError,
					Timeout
			) as e:
				await log(nc, LogLvl.DEBUG, "Ignored error during task polling")
				await asyncio.sleep(5)
				i += 1
				continue
			except NextcloudException as e:
				if e.status_code == 429:
					await log(nc, LogLvl.INFO, "Rate limited during task polling, waiting 10s more")
					await asyncio.sleep(10)
					i += 2
					continue
				raise Exception("Nextcloud error when polling task") from e
			task = Response.model_validate(response).task
			await log(nc, LogLvl.DEBUG, task)
	except ValidationError as e:
		raise Exception("Failed to parse Nextcloud TaskProcessing task result") from e
	if task.status != "STATUS_SUCCESSFUL":
		raise Exception("Nextcloud TaskProcessing Task failed")

	if not isinstance(task.output, dict) or all(x not in ["file", "output", "images", "slide_deck"] for x in task.output):
		raise Exception('"output" key not found in Nextcloud TaskProcessing task result')

	return task