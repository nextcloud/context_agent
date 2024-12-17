import json
import re
import time
import typing
from random import randint
from typing import Optional, Any, Sequence, Union, Callable

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from nc_py_api import Nextcloud
from pydantic import BaseModel, ValidationError

from langchain_core.language_models.chat_models import BaseChatModel


class Task(BaseModel):
	id: int
	status: str
	output: dict[str, str] | None = None


class Response(BaseModel):
	task: Task

# Custom formatting for chat inputs
class ChatWithNextcloud(BaseChatModel):
	tools: Sequence[
		Union[typing.Dict[str, Any], type, Callable, BaseTool]] = []

	def _generate(
			self,
			messages: list[BaseMessage],
			stop: Optional[list[str]] = None,
			run_manager: Optional[CallbackManagerForLLMRun] = None,
			**kwargs: Any,
	) -> ChatResult:
		nc = Nextcloud()
		task_input = dict()

		if messages[0].type == 'system':
			task_input['system_prompt'] = messages[0].content
			messages = messages[1:]

		task_input['tool_message'] = ''
		task_input['tools'] = json.dumps(self.tools)

		history = []
		for message in messages:
			if message.type == 'ai':
				msg = {"role": "assistant", "content": message.content}
				if  len(message.tool_calls) > 0:
					msg['tool_calls'] = message.tool_calls
				history.append(json.dumps(msg))
			elif message.type == 'human':
				history.append(json.dumps({"role": "human", "content": message.content}))
				task_input['input'] = message.content
			elif message.type == 'tool':
				history.append(json.dumps({"role": "tool", "content": message.content, "tool_call_id": message.tool_call_id}))
				task_input['tool_message'] = json.dumps({"name": message.name, "content": message, "tool_call_id": message.tool_call_id})
			else:
				print(message)
				raise Exception("Message type not found")

		if len(history) > 0 and history[-1].startswith('{"role":"human"'):
			history = history[:-1]

		if len(history) > 0 and history[-1].startswith('{"role":"tool"'):
			history = history[:-1]

		task_input['history'] = history

		print(task_input)

		response = nc.ocs(
			"POST",
			"/ocs/v1.php/taskprocessing/schedule",
			json={"type": "core:text2text:chatwithtools", "appId": "context_agent", "input": task_input},
		)

		try:
			task = Response.model_validate(response).task
			print(task)

			i = 0
			# wait for 30 minutes
			while task.status != "STATUS_SUCCESSFUL" and task.status != "STATUS_FAILED" and i < 60 * 6:
				time.sleep(5)
				i += 1
				response = nc.ocs("GET", f"/ocs/v1.php/taskprocessing/task/{task.id}")
				task = Response.model_validate(response).task
				print(task)
		except ValidationError as e:
			raise Exception("Failed to parse Nextcloud TaskProcessing task result") from e

		if task.status != "STATUS_SUCCESSFUL":
			raise Exception("Nextcloud TaskProcessing Task failed")

		if not isinstance(task.output, dict) or "output" not in task.output:
			raise Exception('"output" key not found in Nextcloud TaskProcessing task result')

		if 'tool_calls' in task.output:
			message = AIMessage(task.output['output'], tool_calls=json.loads(task.output['tool_calls']))
		else:
			message = AIMessage(task.output['content'])

		return ChatResult(generations=[ChatGeneration(message=message)])

	def bind_tools(
			self,
			tools: Sequence[
				Union[typing.Dict[str, Any], type, Callable, BaseTool]  # noqa: UP006
			],
			**kwargs: Any,
	) -> Runnable[LanguageModelInput, BaseMessage]:
		self.tools = [convert_to_openai_tool(tool) for tool in tools]
		return self

	def _llm_type(self) -> str:
		return "nextcloud-context-agent"

model = ChatWithNextcloud()