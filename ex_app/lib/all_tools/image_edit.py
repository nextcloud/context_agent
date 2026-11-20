# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.files import get_file_id_by_path, get_file_id_from_file_url
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse, read_only
from ex_app.lib.all_tools.lib.task_processing import run_task

TASK_TYPE = 'core:image2image'


async def _get_file_id(nc: AsyncNextcloudApp, image: str) -> int:
	"""Resolve a file ID, a Nextcloud file link or a path in the user's files to a file ID."""
	# Attachments reach the model only as file IDs (integration_openai adds them to the prompt)
	if image.isdecimal():
		return int(image)
	if image.startswith(('http://', 'https://')):
		return get_file_id_from_file_url(image)
	return await get_file_id_by_path(nc, image)


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def edit_image(images: list[str], prompt: str) -> str:
		"""
		Edit an existing image in Nextcloud with AI (use generate_image for new images)
		:param images: one image (several only to combine them), as a file link (e.g. https://host/index.php/f/1234), a path (e.g. "/Photos/cat.jpg") or a file ID
		:param prompt: the complete change to make; the image model does not see the conversation
		:return: a download link to the edited image
		"""
		if not images:
			raise ValueError('No image given: pass at least one image to edit')
		task_input = {
			'input': [await _get_file_id(nc, image) for image in images],
			'prompt': prompt,
		}
		task = await run_task(nc, TASK_TYPE, task_input)
		url = (await nc.ocs('GET', '/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute', json={'url': 'ocs/v2.php/apps/assistant/api/v1/task'}))["absolute_url"]
		return f"{url}/{task.id}/output-file/{task.output['output']}/download"

	return [
		edit_image,
	]

def get_category_name():
	return "Image editing"

async def is_available(nc: AsyncNextcloudApp):
	tasktypes = (await nc.ocs('GET', '/ocs/v2.php/taskprocessing/tasktypes'))['types'].keys()
	return TASK_TYPE in tasktypes
