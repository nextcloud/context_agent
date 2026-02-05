# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.files import get_file_id_from_file_url
from ex_app.lib.all_tools.lib.task_processing import run_task
from ex_app.lib.all_tools.lib.decorator import safe_tool


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def transcribe_file(file_url: str) -> str:
		"""
		Transcribe a media file stored inside Nextcloud
		:param file_url: The file URL to the media file in nextcloud (e.g. https://host/index.php/f/1234 - The user can input this using the smart picker for example)
		:return: the transcription result
		"""

		task_input = {
			'input': get_file_id_from_file_url(file_url),
		}
		task_output = await run_task(nc,  "core:audio2text", task_input).output
		return task_output['output']

	return [
		transcribe_file,
	]

def get_category_name():
	return "Audio transcription"

async def is_available(nc: AsyncNextcloudApp):
	tasktypes = (await nc.ocs('GET', '/ocs/v2.php/taskprocessing/tasktypes'))['types'].keys()
	return 'core:audio2text' in tasktypes