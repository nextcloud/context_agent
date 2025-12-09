# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import os
import string
import random
from datetime import date

from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from nc_py_api import Nextcloud
from nc_py_api.ex_app import persistent_storage

from ex_app.lib.signature import verify_signature
from ex_app.lib.signature import add_signature
from ex_app.lib.graph import AgentState, get_graph
from ex_app.lib.nc_model import model
from ex_app.lib.tools import get_tools
from ex_app.lib.memorysaver import MemorySaver


# Dummy thread id as we return the whole state
thread = {"configurable": {"thread_id": "thread-1"}}

key_file_path = persistent_storage() + '/secret_key.txt'

if not os.path.exists(key_file_path):
	with open(key_file_path, "w") as file:
		# generate random string of 256 chars
		random_string = ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=256))
		file.write(random_string)
	print(f"The file '{key_file_path}' has been created.")

with open(key_file_path, "r") as file:
	print(f"Reading file '{key_file_path}'.")
	key = file.read()

def load_conversation_old(conversation_token: str):
	checkpointer = MemorySaver()
	if conversation_token == '' or conversation_token == '{}':
		return checkpointer
	checkpointer.storage = checkpointer.serde.loads(verify_signature(conversation_token, key).encode())
	return checkpointer

def load_conversation(conversation_token: str):
	checkpointer = MemorySaver()
	if conversation_token == '' or conversation_token == '{}':
		return checkpointer

	conversation = checkpointer.serde.loads(verify_signature(conversation_token, key).encode())
	last_checkpoint = conversation['last_checkpoint']
	last_config = conversation['last_config']
	checkpointer.storage[last_config['configurable']['thread_id']][last_config['configurable']['checkpoint_ns']][last_config['configurable']['checkpoint_id']] = last_checkpoint
	return checkpointer

def export_conversation(checkpointer):
	last_config = checkpointer.last_config
	last_checkpoint = checkpointer.storage[last_config['configurable']['thread_id']][last_config['configurable']['checkpoint_ns']][last_config['configurable']['checkpoint_id']]
	conversation_token = {"last_config": last_config, "last_checkpoint": last_checkpoint}
	return add_signature(checkpointer.serde.dumps(conversation_token).decode('utf-8'), key)


async def react(task, nc: Nextcloud):
	safe_tools, dangerous_tools = await get_tools(nc)

	model.bind_nextcloud(nc)

	tools = dangerous_tools + safe_tools

	bound_model = model.bind_tools(
		tools,
	)

	def tool_enabled(tool_name):
		for tool in tools:
			if tool.name == tool_name:
				return True
		return False

	async def call_model(
			state: AgentState,
			config: RunnableConfig,
	):
		current_date = date.today().strftime("%Y-%m-%d")

		system_prompt_text = """
You are a helpful AI assistant with access to tools, please respond to the user's query to the best of your ability, using the provided tools if necessary. If no tool is needed to provide a correct answer, do not use one. If you used a tool, you still need to convey its output to the user.
Use the same language for your answers as the user used in their message.
Today is {CURRENT_DATE}.
Intuit the language the user is using (there is no tool for this, you will need to guess). Reply in the language intuited. Do not output the language you intuited.
Only use tools if you cannot answer the user without them.
If you get a link as a tool output, always add the link to your response.
"""
		if tool_enabled("duckduckgo_results_json"):
			system_prompt_text += "Only use the duckduckgo_results_json tool if the user explicitly asks for a web search.\n"
		if tool_enabled("list_talk_conversations"):
			system_prompt_text += "Use the list_talk_conversations tool to check which conversations exist.\n"
		if tool_enabled("list_calendars"):
			system_prompt_text += "Use the list_calendars tool to check which calendars exist.\nIf an item should be added to a list, check list_calendars for a fitting calendar and add the item as a task there.\n"
		if tool_enabled("find_person_in_contacts"):
			system_prompt_text += "Use the find_person_in_contacts tool to find a person's email address and location.\n"
		if tool_enabled("find_details_of_current_user"):
			system_prompt_text += "Use the find_details_of_current_user tool to find the current user's location.\n"
		# this is similar to customizing the create_react_agent with state_modifier, but is a lot more flexible
		system_prompt = SystemMessage(
			system_prompt_text.replace("{CURRENT_DATE}", current_date)
		)

		response = await bound_model.ainvoke([system_prompt] + state["messages"], config)
		# We return a list, because this will get added to the existing list
		return {"messages": [response]}

	try:
		checkpointer = load_conversation(task['input']['conversation_token'])
	except Exception as e:
		checkpointer = load_conversation_old(task['input']['conversation_token'])

	graph = await get_graph(call_model, safe_tools, dangerous_tools, checkpointer)

	state_snapshot = graph.get_state(thread)

	## if the next step is a tool call
	if state_snapshot.next == ('dangerous_tools', ):
		if task['input']['confirmation'] == 0:
			new_input = {
				"messages": [
					ToolMessage(
						tool_call_id=tool_call["id"],
						content=f"API call denied by user. Reasoning: '{task['input']['input']}'. Continue assisting, accounting for the user's input.",
					)
					for tool_call in state_snapshot.values['messages'][-1].tool_calls
				]
			}
		else:
			new_input = None
	else:
		new_input = {"messages": [("user", task['input']['input'])]}

	async for event in graph.astream(new_input, thread, stream_mode="values"):
		last_message = event['messages'][-1]
		for message in event['messages']:
			if isinstance(message, HumanMessage):
				source_list = []
			if isinstance(message, AIMessage) and message.tool_calls:
					for tool_call in message.tool_calls:
						source_list.append(tool_call['name'])

	state_snapshot = graph.get_state(thread)
	actions = ''
	if state_snapshot.next == ('dangerous_tools', ):
		actions = json.dumps(last_message.tool_calls)

	return {
		'output': last_message.content,
		'actions': actions,
		'conversation_token': export_conversation(checkpointer),
		'sources': source_list,
	}
