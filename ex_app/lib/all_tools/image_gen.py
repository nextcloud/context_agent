# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import Nextcloud

from ex_app.lib.all_tools.lib.files import get_file_id_from_file_url
from ex_app.lib.all_tools.lib.task_processing import run_task
from ex_app.lib.all_tools.lib.decorator import safe_tool


def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def generate_image(input: str) -> str:
		"""
		Generate an image with the input string as description
		:param text: the instructions for the image generation
		:return: a download link to the generated image
		"""
		tasktype = "core:text2image"
		task_input = {
			'input': input,
			'numberOfImages': 1,
		}
		task = run_task(nc,  tasktype, task_input)

		return f"https://nextcloud.local/ocs/v2.php/apps/assistant/api/v1/task/{task.id}/output-file/{task.output['images'][0]}/download"

	return [
		generate_image,
	]

def get_category_name():
	return "Image generation"

def is_available(nc: Nextcloud):
	tasktypes = nc.ocs('GET', '/ocs/v2.php/taskprocessing/tasktypes')['types'].keys()
	return 'core:text2image' in tasktypes