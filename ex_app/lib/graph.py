# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Sequence

from langchain_core.messages import ToolMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict, Annotated


class AgentState(TypedDict):
	"""The state of the agent."""

	# add_messages is a reducer
	# See https://langchain-ai.github.io/langgraph/concepts/low_level/#reducers
	messages: Annotated[Sequence[BaseMessage], add_messages]


def handle_tool_error(state) -> dict:
	error = state.get("error")
	tool_calls = state["messages"][-1].tool_calls
	return {
		"messages": [
			ToolMessage(
				content=f"Error: {repr(error)}\n please fix your mistakes.",
				tool_call_id=tc["id"],
			)
			for tc in tool_calls
		]
	}

def create_tool_node_with_fallback(tools: list) -> dict:
	return ToolNode(tools).with_fallbacks(
		[RunnableLambda(handle_tool_error)], exception_key="error"
	)

def get_graph(call_model, safe_tools, dangerous_tools, checkpointer):
	dangerous_tool_names = {tool.name: tool for tool in dangerous_tools}
	safe_tool_names = {tool.name: tool for tool in safe_tools}

	# Define a new graph
	workflow = StateGraph(AgentState)

	# Define the two nodes we will cycle between
	workflow.add_node("agent", call_model)
	workflow.add_node("safe_tools", create_tool_node_with_fallback(safe_tools))
	workflow.add_node("dangerous_tools", create_tool_node_with_fallback(dangerous_tools))

	# Set the entrypoint as `agent`
	# This means that this node is the first one called
	workflow.set_entry_point("agent")

	def route_tools(state: AgentState):
		next_node = tools_condition(state)
		# If no tools are invoked, return to the user
		if next_node == END:
			return END
		ai_message = state["messages"][-1]
		# This assumes single tool calls. To handle parallel tool calling, you'd want to
		# use an ANY condition
		first_tool_call = ai_message.tool_calls[0]
		print('Tool call: ', first_tool_call)
		if first_tool_call["name"] in dangerous_tool_names:
			return "dangerous_tools"
		return "safe_tools"

	workflow.add_conditional_edges(
		"agent", route_tools, ["safe_tools", "dangerous_tools", END]
	)
	workflow.add_edge("safe_tools", "agent")
	workflow.add_edge("dangerous_tools", "agent")

	# Now we can compile and visualize our graph
	graph = workflow.compile(
		checkpointer=checkpointer,
		interrupt_before=["dangerous_tools"],
		debug=False
	)

	return graph