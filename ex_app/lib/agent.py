import json
import os
import string
import random
from datetime import date

from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from nc_py_api import Nextcloud
from nc_py_api.ex_app import persistent_storage

from ex_app.lib.signature import verify_signature
from ex_app.lib.signature import add_signature
from ex_app.lib.graph import AgentState, get_graph
from ex_app.lib.nc_model import model
from ex_app.lib.tools import get_tools

from langchain_community.tools import YouTubeSearchTool

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

def load_conversation(checkpointer, conversation_token: str):
	if conversation_token == '' or conversation_token == '{}':
		return
	checkpointer.storage = checkpointer.serde.loads(verify_signature(conversation_token, key).encode())

def export_conversation(checkpointer):
	return add_signature(checkpointer.serde.dumps(checkpointer.storage).decode('utf-8'), key)


def react(task, nc: Nextcloud):
	safe_tools, dangerous_tools = get_tools(nc)
	safe_tools.append(YouTubeSearchTool())

	model.bind_nextcloud(nc)

	bound_model = model.bind_tools(
		dangerous_tools
		+ safe_tools
	)

	def call_model(
			state: AgentState,
			config: RunnableConfig,
	):
		current_date = date.today().strftime("%Y-%m-%d")
		# this is similar to customizing the create_react_agent with state_modifier, but is a lot more flexible
		system_prompt = SystemMessage(
"""
You are a helpful AI assistant with access to tools, please respond to the user's query to the best of your ability, using the provided tools! If you used a tool, you still need to convey its output to the user.
Use the same language for your answers as the user used in their message.
Today is {CURRENT_DATE}.
Detect the language the user is using. Reply in the detected language. Do not output the detected language.
You can check which conversations exist using the list_talk_conversations tool, if a conversation cannot be found.
You can check which calendars exist using the list_calendars tool, if a calendar can not be found.
you can find out a user's email address and location by using the find_person_in_contacts tool.
you can find out the current user's location by using the find_details_of_current_user tool.
If an item should be added to a list, check list_calendars for a fitting calendar and add the item as a task there.
Always let the user know where you got the information.
""".replace("{CURRENT_DATE}", current_date)
		)

		response = bound_model.invoke([system_prompt] + state["messages"], config)
		# We return a list, because this will get added to the existing list
		return {"messages": [response]}

	checkpointer = MemorySaver()
	graph = get_graph(call_model, safe_tools, dangerous_tools, checkpointer)

	load_conversation(checkpointer, task['input']['conversation_token'])

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

	for event in graph.stream(new_input, thread, stream_mode="values"):
		last_message = event['messages'][-1]

	state_snapshot = graph.get_state(thread)
	actions = ''
	if state_snapshot.next == ('dangerous_tools', ):
		actions = json.dumps(last_message.tool_calls)

	return {
		'output': last_message.content,
		'actions': actions,
		'conversation_token': export_conversation(checkpointer)
	}