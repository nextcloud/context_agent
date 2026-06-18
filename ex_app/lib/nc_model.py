# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import typing
from collections.abc import AsyncIterator
from typing import Optional, Any, Sequence, Union, Callable

from niquests import ConnectionError, Timeout
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage, AIMessage, AIMessageChunk
from langchain_core.messages.tool import default_tool_parser, invalid_tool_call, tool_call
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from nc_py_api import AsyncNextcloudApp, NextcloudException
from nc_py_api.ex_app import LogLvl
from pydantic import BaseModel, ValidationError

from langchain_core.language_models.chat_models import BaseChatModel

from ex_app.lib.logger import log


class Task(BaseModel):
	id: int
	status: str
	output: dict[str, typing.Any] | None = None
	preferStreaming: bool | None = None


class Response(BaseModel):
	task: Task

# Custom formatting for chat inputs
class ChatWithNextcloud(BaseChatModel):
	nc: AsyncNextcloudApp = AsyncNextcloudApp()
	tools: Sequence[
		Union[typing.Dict[str, Any], type, Callable, BaseTool]] = []
	TIMEOUT: int = 60 * 30 # 30 minutes
	MAX_MESSAGE_HISTORY: int = 42
	TOOL_OUTPUT_TRUNCATE_AFTER: int = 10
	TOOL_OUTPUT_MAX_LENGTH: int = 2000
	POLL_WAIT_TIME: int = 5
	STREAMING_POLL_WAIT_TIME: int = 1

	def _generate(self, messages: list[BaseMessage], stop: Optional[list[str]] = None, run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any):
		raise Exception("Use _agenerate instead")

	def _build_task_input(self, messages: list[BaseMessage]) -> dict[str, typing.Any]:
		task_input = dict()

		if messages[0].type == 'system':
			task_input['system_prompt'] = messages[0].content
			messages = messages[1:]

		# Impose a history limit on non-tool messages to avoid token intake exploding.
		# Tool messages don't count toward the limit so tool_call/tool_result pairs
		# stay intact even in tool-heavy conversations.
		non_tool_count = 0
		cutoff_idx = 0
		# idx=0 is the first message, idx=len-1 is the most recent, so we walk backward
		for i in range(len(messages) - 1, -1, -1):
			if messages[i].type != 'tool':
				non_tool_count += 1
				if non_tool_count > self.MAX_MESSAGE_HISTORY:
					cutoff_idx = i + 1
					break
		messages = messages[cutoff_idx:]

		# first message cannot be a tool message
		while len(messages) > 0 and messages[0].type == 'tool':
			messages = messages[1:]

		task_input['input'] = ''
		task_input['tool_message'] = []
		task_input['tools'] = json.dumps(self.tools)

		history = []
		for i, message in enumerate(messages):
			if message.type == 'ai':
				msg = {"role": "assistant", "content": message.content}
				if len(message.tool_calls) > 0:
					msg['tool_calls'] = message.tool_calls
				history.append(json.dumps(msg))
			elif message.type == 'human':
				if len(messages)-1 != i:
					history.append(json.dumps({"role": "human", "content": message.content}))
				else:
					task_input['input'] = message.content
			elif message.type == 'tool':
				content = message.content
				age = len(messages) - 1 - i
				if age > self.TOOL_OUTPUT_TRUNCATE_AFTER and isinstance(content, str) and len(content) > self.TOOL_OUTPUT_MAX_LENGTH:
					content = content[:self.TOOL_OUTPUT_MAX_LENGTH] + "…[truncated]"
				if len(messages)-1 != i:
					history.append(json.dumps({"role": "tool", "content": content, "name": message.name, "tool_call_id": message.tool_call_id}))
				else:
					task_input['tool_message'].append({"name": message.name, "content": content, "tool_call_id": message.tool_call_id})
			else:
				print(message)
				raise Exception("Message type not found")

		task_input['history'] = history
		if len(task_input['tool_message']) > 0:
			task_input['tool_message'] = json.dumps(task_input['tool_message'])
		else:
			task_input['tool_message'] = ''

		return task_input

	async def _schedule_task(self, task_input: dict[str, typing.Any], prefer_streaming: bool = False) -> Task:
		nc = self.nc

		await log(nc, LogLvl.DEBUG, task_input)

		i = 0
		while i < 20:
			try:
				response = await nc.ocs(
					"POST",
					"/ocs/v1.php/taskprocessing/schedule",
					json={
						"type": "core:text2text:chatwithtools",
						"appId": "context_agent",
						"input": task_input,
						"preferStreaming": prefer_streaming,
					},
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
			return task
		except ValidationError as e:
			raise Exception("Failed to parse Nextcloud TaskProcessing task result") from e

	async def _poll_task(self, task_id: int, wait_time: int) -> Task:
		nc = self.nc
		try:
			response = await nc.ocs("GET", f"/ocs/v1.php/taskprocessing/task/{task_id}")
		except (
				ConnectionError,
				Timeout
		) as e:
			await log(nc, LogLvl.DEBUG, "Ignored error during task polling")
			await asyncio.sleep(wait_time)
			raise
		except NextcloudException as e:
			if e.status_code == 429:
				await log(nc, LogLvl.INFO, "Rate limited during task polling, waiting 10s more")
				await asyncio.sleep(10)
				raise
			raise Exception("Nextcloud error when polling task") from e

		try:
			task = Response.model_validate(response).task
			await log(nc, LogLvl.DEBUG, task)
			return task
		except ValidationError as e:
			raise Exception("Failed to parse Nextcloud TaskProcessing task result") from e

	def _task_output_text(self, task: Task) -> str | None:
		if not isinstance(task.output, dict):
			return None
		output = task.output.get('output')
		return output if isinstance(output, str) else None

	def _raw_task_tool_calls(self, task: Task) -> list[dict[str, typing.Any]]:
		if not isinstance(task.output, dict):
			return []
		raw_tool_calls = task.output.get('tool_calls')
		if raw_tool_calls in (None, ''):
			return []
		if isinstance(raw_tool_calls, str):
			parsed_tool_calls = json.loads(raw_tool_calls)
		else:
			parsed_tool_calls = raw_tool_calls
		if not isinstance(parsed_tool_calls, list):
			raise Exception('Invalid "tool_calls" value in Nextcloud TaskProcessing task result')
		return parsed_tool_calls

	def _task_tool_calls(self, task: Task) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
		raw_tool_calls = self._raw_task_tool_calls(task)
		if len(raw_tool_calls) == 0:
			return [], []

		if all(isinstance(raw_tool_call, dict) and 'name' in raw_tool_call and 'args' in raw_tool_call for raw_tool_call in raw_tool_calls):
			parsed_tool_calls = []
			invalid_tool_calls = []
			for raw_tool_call in raw_tool_calls:
				args = raw_tool_call.get('args', {})
				if isinstance(args, str):
					try:
						args = json.loads(args)
					except json.JSONDecodeError:
						invalid_tool_calls.append(invalid_tool_call(
							name=raw_tool_call.get('name'),
							args=raw_tool_call.get('args'),
							id=raw_tool_call.get('id'),
							error=None,
						))
						continue
				if not isinstance(args, dict):
					invalid_tool_calls.append(invalid_tool_call(
						name=raw_tool_call.get('name'),
						args=json.dumps(raw_tool_call.get('args')),
						id=raw_tool_call.get('id'),
						error=None,
					))
					continue
				parsed_tool_calls.append(tool_call(
					name=raw_tool_call.get('name') or '',
					args=args,
					id=raw_tool_call.get('id'),
				))
			return parsed_tool_calls, invalid_tool_calls

		return default_tool_parser(raw_tool_calls)

	def _task_to_message(self, task: Task) -> AIMessage:
		if not isinstance(task.output, dict) or "output" not in task.output:
			raise Exception('"output" key not found in Nextcloud TaskProcessing task result')

		tool_calls, invalid_tool_calls = self._task_tool_calls(task)
		if len(tool_calls) > 0 or len(invalid_tool_calls) > 0:
			message = AIMessage(task.output['output'], tool_calls=tool_calls, invalid_tool_calls=invalid_tool_calls)
		else:
			message = AIMessage(task.output['output'])

		return message

	def _stream_delta(self, streamed_output: str, current_output: str) -> str:
		if current_output.startswith(streamed_output):
			return current_output[len(streamed_output):]
		return current_output

	async def _agenerate(
			self,
			messages: list[BaseMessage],
			stop: Optional[list[str]] = None,
			run_manager: Optional[CallbackManagerForLLMRun] = None,
			**kwargs: Any,
	) -> ChatResult:
		task_input = self._build_task_input(messages)
		task = await self._schedule_task(task_input)

		i = 0
		wait_time = self.POLL_WAIT_TIME
		# wait for TIMEOUT (one i ^= wait_time sec)
		while task.status != "STATUS_SUCCESSFUL" and task.status != "STATUS_FAILED" and i < self.TIMEOUT / wait_time:
			await asyncio.sleep(wait_time)
			i += 1
			try:
				task = await self._poll_task(task.id, wait_time)
			except (ConnectionError, Timeout):
				i += 1
				continue
			except NextcloudException as e:
				if e.status_code == 429:
					i += 2
					continue
				raise

		if task.status == "STATUS_FAILED":
			raise Exception("Nextcloud TaskProcessing Task failed")

		if task.status in ("STATUS_RUNNING", "STATUS_SCHEDULED"):
			raise Exception("Nextcloud TaskProcessing Task timed out")

		message = self._task_to_message(task)
		return ChatResult(generations=[ChatGeneration(message=message)])

	async def _astream(
			self,
			messages: list[BaseMessage],
			stop: Optional[list[str]] = None,
			**kwargs: Any,
	) -> AsyncIterator[ChatGenerationChunk]:
		task_input = self._build_task_input(messages)
		task = await self._schedule_task(task_input, prefer_streaming=True)

		streamed_output = ''
		streaming_supported = False
		yielded_chunk = False
		i = 0
		wait_time = self.STREAMING_POLL_WAIT_TIME

		while task.status not in ("STATUS_SUCCESSFUL", "STATUS_FAILED") and i < self.TIMEOUT / wait_time:
			await asyncio.sleep(wait_time)
			i += 1
			try:
				task = await self._poll_task(task.id, wait_time)
			except (ConnectionError, Timeout):
				i += 1
				continue
			except NextcloudException as e:
				if e.status_code == 429:
					i += 10
					continue
				raise

			current_output = self._task_output_text(task)
			if task.status == "STATUS_RUNNING" and current_output is not None:
				streaming_supported = True
				delta = self._stream_delta(streamed_output, current_output)
				if delta:
					yielded_chunk = True
					yield ChatGenerationChunk(message=AIMessageChunk(content=delta))
				streamed_output = current_output

		if task.status == "STATUS_FAILED":
			raise Exception("Nextcloud TaskProcessing Task failed")

		if task.status in ("STATUS_RUNNING", "STATUS_SCHEDULED"):
			raise Exception("Nextcloud TaskProcessing Task timed out")

		final_output = self._task_output_text(task)
		if final_output is None:
			raise Exception('"output" key not found in Nextcloud TaskProcessing task result')

		if not streaming_supported:
			if final_output:
				yielded_chunk = True
				yield ChatGenerationChunk(message=AIMessageChunk(content=final_output))
		else:
			final_delta = self._stream_delta(streamed_output, final_output)
			if final_delta:
				yielded_chunk = True
				yield ChatGenerationChunk(message=AIMessageChunk(content=final_delta))

		tool_calls, invalid_tool_calls = self._task_tool_calls(task)
		if len(tool_calls) > 0 or len(invalid_tool_calls) > 0:
			yielded_chunk = True
			yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_calls=tool_calls, invalid_tool_calls=invalid_tool_calls))

		if not yielded_chunk:
			yield ChatGenerationChunk(message=AIMessageChunk(content=''))

	def bind_tools(
			self,
			tools: Sequence[
				Union[typing.Dict[str, Any], type, Callable, BaseTool]  # noqa: UP006
			],
			**kwargs: Any,
	) -> Runnable[LanguageModelInput, BaseMessage]:
		self.tools = [convert_to_openai_tool(tool) for tool in tools]
		return self

	def bind_nextcloud(self,
					   nc: AsyncNextcloudApp):
		self.nc = nc

	def _llm_type(self) -> str:
		return "nextcloud-context-agent"

model = ChatWithNextcloud()