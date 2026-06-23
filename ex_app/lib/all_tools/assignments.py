# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import datetime

import pytz
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import dangerous_tool, safe_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@dangerous_tool
	async def create_scheduled_task(title: str, prompt: str, recurrence_rule: str, timezone: str|None = None, starts_at: None|str = None):
		"""
		Create a Scheduled Task for the assistant that will be carried out autonomously.
		The user will still have to approve sensitive actions.
		For example, the user could ask to transcribe new files in a certain folder every hour. Then the
		prompt argument for this tool would be "Transcribe new files in folder /Audio" and the recurrence_rule would be "FREQ=HOURLY".
		After having created the Scheduled Task, let the user know that the Scheduled Task will run in a newly created chat session.
		:param title: A title for the Scheduled Task, e.g. "Transcribe audio files" -- This is only for the user's reference and has no effect on the execution of the Scheduled Task.
		:param prompt: The instructions for the agent carrying out the Scheduled Task (Do not tell it to create a scheduled task here, as this is the instruction that runs as the scheduled task already. So instead of "Create a scheduled task to do X", just pass "Do X" here)
		:param recurrence_rule: An RRule compliant with RFC 5545 that defines the recurrence rule for the Scheduled Task. For example "FREQ=DAILY;INTERVAL=1" to run the Scheduled Task every day, an empty string as the recurrence_rule means the task does not repeat.
		:param starts_at: A date time string in ISO 8601 format that defines when the Scheduled Task should start. For example "2025-01-01T09:00:00Z". If not provided, the Scheduled Task will start immediately. Make sure to use the user's timezone for this, obtainable with find_details_of_current_user
		:param timezone: Timezone (e.g., 'America/New_York') defaults to the user's current time zone
		:return:
		"""

		await nc.ocs('POST', f'/ocs/v2.php/apps/assistant/assignments', json={
			'title': title,
			'prompt': prompt,
			'recurrence': recurrence_rule,
			'startsAt': int(datetime.datetime.fromisoformat(starts_at.replace('Z', '+00:00')).timestamp()) if starts_at is not None else datetime.datetime.now(datetime.UTC).timestamp(),
			'timezone': pytz.timezone(timezone).zone if timezone is not None else None
		})

		return True

	@tool
	@safe_tool
	async def list_scheduled_tasks():
		"""
		List all assistant Scheduled Tasks by the current user.
		:return:
		"""

		return await nc.ocs('GET', f'/ocs/v2.php/apps/assistant/assignments')

	@tool
	@dangerous_tool
	async def update_scheduled_task(id: int, prompt: None|str = None, recurrence_rule: None|str = None, timezone: str|None = None, starts_at: None|str = None):
		"""
		Update a assistant Scheduled Task
		:param id: The ID of the Scheduled Task to update, you can obtain this from the list_scheduled_tasks tool
		:param prompt: The instructions for the AI carrying out the Scheduled Task. Pass `None` to leave this unchanged. (Do not tell the other AI to create a scheduled task here, as this is the instruction that runs as the scheduled task already. So instead of "Create a scheduled task to do X", just pass "Do X" here)
		:param recurrence_rule: An RRule compliant with RFC 5545 that defines the recurrence rule for the Scheduled Task. For example "FREQ=DAILY;INTERVAL=1" to run the Scheduled Task every day. An empty string means, it does not repeat. Pass `None` to leave this unchanged.
		:param timezone A timezone for the scheduled task, set to None to leave this as is.
		:param starts_at: A date time string in ISO 8601 format that defines when the Scheduled Task should start. For example "2025-01-01T09:00:00Z". If not provided, the Scheduled Task will start immediately. Pass `None` to leave this unchanged.
		:return:
		"""

		return await nc.ocs('PATCH', f'/ocs/v2.php/apps/assistant/assignments/{id}', json={
			'prompt': prompt,
			'recurrence': recurrence_rule,
			'startsAt': int(datetime.datetime.fromisoformat(starts_at.replace('Z', '+00:00')).timestamp()) if starts_at is not None else None,
			'timezone': pytz.timezone(timezone).zone if timezone is not None else None
		})

	@tool
	@dangerous_tool
	async def delete_scheduled_task(id: int):
		"""
		Delete a recurring Assistant Scheduled Task
		:param id: The ID of the Scheduled Task to delete, you can obtain this from the list_scheduled_tasks tool
		:return:
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/assistant/assignments/{id}')

	return [
		create_scheduled_task,
		list_scheduled_tasks,
		update_scheduled_task,
		delete_scheduled_task,
	]

def get_category_name():
	return "Assistant Scheduled Tasks"

async def is_available(nc: AsyncNextcloudApp):
	return True