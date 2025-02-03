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

	return [
		ask_context_chat,
		transcribe_file,
	]