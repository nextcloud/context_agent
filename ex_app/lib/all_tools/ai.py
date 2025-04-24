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
	def ask_context_chat(question: str) -> str:
		"""
		Ask the context chat oracle, which knows all of the user's documents, a question about them
		:param question: The question to ask
		:return: the answer from context chat
		"""

		task_input = {
			'prompt': question,
			'scopeType': 'none',
			'scopeList': [],
			'scopeListMeta': '',
		}
		task_output = run_task(nc,  "context_chat:context_chat", task_input)
		return task_output['output']

	@tool
	@safe_tool
	def transcribe_file(file_url: str) -> str:
		"""
		Transcribe a media file stored inside the nextcloud
		:param file_url: The file URL to the media file in nextcloud (The user can input this using the smart picker for example)
		:return: the transcription result
		"""
		task_input = {
			'input': get_file_id_from_file_url(file_url),
		}
		task_output = run_task(nc,  "core:audio2text", task_input)
		return task_output['output']


	@tool
	@safe_tool
	def generate_document(input: str) -> str:
		"""
		Generate a document with the input string as description
		:param text: the instructions for the document
		:return: a download link to the generated document
		"""

		task_input = {
			'text': input,
		}
		task_output = run_task(nc,  "richdocuments:text_to_text_document", task_input)
		return f"https://nextcloud.local/ocs/v2.php/apps/assistant/api/v1/task/{task_output['task_id']}/output-file/{task_output['file']}/download"

	return [
		ask_context_chat,
		transcribe_file,
		generate_document,
	]