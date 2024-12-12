import json

from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from nc_py_api import Nextcloud

from signature import verify_signature
from signature import add_signature
from graph import AgentState, get_graph
from nc_model import model
from tools import get_tools

cloud_nc_com = Nextcloud(nextcloud_url="https://cloud.nextcloud.com", nc_auth_user="", nc_auth_pass="")

# Dummy thread id as we return the whole state
thread = {"configurable": {"thread_id": "thread-1"}}

key = 'CHANGEME'

def load_conversation(checkpointer, conversation_token: str):
	if conversation_token == '' or conversation_token == '{}':
		return
	checkpointer.storage = checkpointer.serde.loads(verify_signature(conversation_token, key).encode())

def export_conversation(checkpointer):
	return add_signature(checkpointer.serde.dumps(checkpointer.storage).decode('utf-8'), key)


def react(task, nc: Nextcloud):
	safe_tools, dangerous_tools = get_tools(cloud_nc_com)

	bound_model = model.bind_tools(
		dangerous_tools
		+ safe_tools
	)

	def call_model(
			state: AgentState,
			config: RunnableConfig,
	):
		# this is similar to customizing the create_react_agent with state_modifier, but is a lot more flexible
		system_prompt = SystemMessage(
"""
You are a helpful AI assistant with access to tools, please respond to the user's query to the best of your ability, using the provided tools! If you used a tool, you still need to convey its output to the user.
Use the same language for your answers as the user used in their message.
Today is 2024-12-02.
The user's timezone is Europe/London.
Detect the language the user is using. Reply in the same language.
You can check which conversations exist using the list_talk_conversations tool, if a conversation cannot be found.
You can check which calendars exist using the list_calendars tool, if a calendar can not be found.
"""
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
						tool_call_id=state_snapshot.values['messages'][-1].tool_calls[0]["id"],
						content=f"API call denied by user. Reasoning: '{task['input']['message']}'. Continue assisting, accounting for the user's input.",
					)
				]
			}
		else:
			new_input = None
	else:
		new_input = {"messages": [("user", task['input']['message'])]}

	for event in graph.stream(new_input, thread, stream_mode="values"):
		last_message = event['messages'][-1]

	state_snapshot = graph.get_state(thread)
	actions = ''
	if state_snapshot.next == ('dangerous_tools', ):
		actions = json.dumps(last_message.tool_calls)

	return {
		'response': last_message.content,
		'actions': actions,
		'conversation_token': export_conversation(checkpointer)
	}