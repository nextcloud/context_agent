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
		Generate a document with the input string as description. 
		:param text: the instructions for the document
		:param format: the format of the generated file, allowed values are "text document", "pdf", "spreadsheet", "excel spreadsheet" and "slides"
		:return: a download link to the generated document
		"""
		url = nc.ocs('GET', '/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute', json={'url': 'ocs/v2.php/apps/assistant/api/v1/task'})

		match format:
			case "text document":
				tasktype = "richdocuments:text_to_text_document"
				task_input = {
					'text': input,
					'target_format': 'docx'
				}
			
			case "pdf": 
				tasktype = "richdocuments:text_to_text_document"
				task_input = {
					'text': input,
					'target_format': 'pdf'
				}

			case "spreadsheet":
				tasktype = "richdocuments:text_to_spreadsheet_document"
				task_input = {
					'text': input,
					'target_format': 'ods'
				}

			case "excel spreadsheet":
				tasktype = "richdocuments:text_to_spreadsheet_document"
				task_input = {
					'text': input,
					'target_format': 'xlsx'
				}

			case "slides":
				tasktype = "richdocuments:slide_deck_generation"
				task_input = {
					'text': input,
				}
				task = run_task(nc,  tasktype, task_input)
				return f"{url}/{task.id}/output-file/{task.output['slide_deck']}/download"

		task = run_task(nc,  tasktype, task_input)
		return f"{url}/{task.id}/output-file/{task.output['file']}/download"

	return [
		generate_document,
	]

def get_category_name():
	return "Office document generation"

def is_available(nc: Nextcloud):
	return 'richdocuments' in nc.capabilities