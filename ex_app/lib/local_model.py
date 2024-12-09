import json
import re
import typing
from random import randint
from typing import Optional, Any, Sequence, Union, Callable

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from transformers import AutoModelForCausalLM, AutoTokenizer

model_name_or_path = "Qwen/Qwen2.5-3B-Instruct"

tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)
llm = AutoModelForCausalLM.from_pretrained(
	model_name_or_path,
	torch_dtype="auto",
	device_map="auto",
)


from langchain_core.language_models.chat_models import BaseChatModel


def try_parse_tool_calls(content: str):
	"""Try parse the tool calls."""
	tool_calls = []
	offset = 0
	for i, m in enumerate(re.finditer(r"<tool_call>\n(.+)?\n</tool_call>", content)):
		if i == 0:
			offset = m.start()
		try:
			func = json.loads(m.group(1))
			tool_calls.append(func)
			if isinstance(func["arguments"], str):
				func["arguments"] = json.loads(func["arguments"])
			if 'arguments' in func:
				func['args'] = func['arguments']
				del func['arguments']
			if not 'id' in func:
				func['id'] = str(randint(1, 10000000000))
		except json.JSONDecodeError as e:
			print(f"Failed to parse tool calls: the content is {m.group(1)} and {e}")
			pass
	if tool_calls:
		if offset > 0 and content[:offset].strip():
			c = content[:offset]
		else:
			c = ""
		return {"role": "assistant", "content": c, "tool_calls": tool_calls}
	return {"role": "assistant", "content": re.sub(r"<\|im_end\|>$", "", content)}

def convert_langchain_messages_to_transformers_messages(messages: list[BaseMessage]):
	return [dict(role=dict(ai='assistant', human='user', system='system', tool='tool')[message.type], content=message.content) for message in messages]

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
		text = tokenizer.apply_chat_template(
			conversation=convert_langchain_messages_to_transformers_messages(messages),
			tokenize=False,
			tools=self.tools,
			add_generation_prompt=True,
		)
		model_inputs = tokenizer([text], return_tensors="pt").to(llm.device)

		generated_ids = llm.generate(
			**model_inputs,
			max_new_tokens=512,
		)

		generated_ids = [
			output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
		]

		response = tokenizer.batch_decode(generated_ids)[0]

		response = try_parse_tool_calls(response)

		print(response)

		if 'tool_calls' in response:
			message = AIMessage(response['content'], tool_calls=response['tool_calls'])
		else:
			message = AIMessage(response['content'])

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