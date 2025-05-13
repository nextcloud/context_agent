# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import Nextcloud

from ex_app.lib.all_tools.lib.task_processing import run_task
from ex_app.lib.all_tools.lib.decorator import safe_tool


def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def generate_document(input: str, format: str) -> str:
		"""
		Generate a document with the input string as description
		:param text: the instructions for the document
		:param format: the format of the generated file, available are "text_document" and "spreadsheet_document"
		:return: a download link to the generated document
		"""
		tasktype = "richdocuments:text_to_" + format
		task_input = {
			'text': input,
		}
		task = run_task(nc,  tasktype, task_input)
		return f"https://nextcloud.local/ocs/v2.php/apps/assistant/api/v1/task/{task.id}/output-file/{task.output['file']}/download"

	return [
		generate_document,
	]

def get_category_name():
	return "Office Document Generation"