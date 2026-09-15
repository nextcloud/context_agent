# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import traceback
from typing import Sequence

from langchain_core.messages import ToolMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict, Annotated

from ex_app.lib.all_tools.lib.impulse import (
	DEFAULT_DESTRUCTIVE_THRESHOLD,
	DEFAULT_IMPULSE_RADIUS,
	DEFAULT_IMPULSE_THRESHOLD,
	ImpulseRadius,
	classify_tool_call,
	is_destructive,
	needs_confirmation,
)

# The two tool nodes hold the same tools; only one of them is interrupted before.
# Which one a call is routed to is decided per call from its impulse radius.
# The node names are part of the persisted conversation state, so they are kept
# as-is to not break conversations that are waiting for a confirmation across an
# app update.
AUTO_TOOLS_NODE = "safe_tools"
CONFIRM_TOOLS_NODE = "dangerous_tools"


class AgentState(TypedDict):
	"""The state of the agent."""

	# add_messages is a reducer
	# See https://langchain-ai.github.io/langgraph/concepts/low_level/#reducers
	messages: Annotated[Sequence[BaseMessage], add_messages]


def handle_tool_error(state) -> dict:
	error = state.get("error")
	tool_calls = state["messages"][-1].tool_calls
	lines = traceback.format_exception(error)
	print("\n".join(lines))
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

async def get_graph(
	call_model,
	tools,
	checkpointer,
	impulse_threshold: ImpulseRadius = DEFAULT_IMPULSE_THRESHOLD,
	destructive_threshold: ImpulseRadius = DEFAULT_DESTRUCTIVE_THRESHOLD,
):
	tools_by_name = {tool.name: tool for tool in tools}

	# Define a new graph
	workflow = StateGraph(AgentState)

	# Define the two nodes we will cycle between
	workflow.add_node("agent", call_model)
	workflow.add_node(AUTO_TOOLS_NODE, create_tool_node_with_fallback(tools))
	workflow.add_node(CONFIRM_TOOLS_NODE, create_tool_node_with_fallback(tools))

	# Set the entrypoint as `agent`
	# This means that this node is the first one called
	workflow.set_entry_point("agent")

	async def classify_pending_calls(state: AgentState) -> tuple[ImpulseRadius, bool]:
		"""The widest radius the pending tool calls reach, and whether any deletes."""
		radius = ImpulseRadius.SELF
		destroys = False
		for tool_call in state["messages"][-1].tool_calls:
			tool = tools_by_name.get(tool_call["name"])
			if tool is None:
				# The model hallucinated a tool; the tool node will error out on it,
				# but until then treat it as the widest reach.
				call_radius, call_destroys = DEFAULT_IMPULSE_RADIUS, False
			else:
				call_radius = await classify_tool_call(tool, tool_call.get("args") or {})
				call_destroys = is_destructive(tool)
			print(f"Tool call: {tool_call['name']} -> impulse radius {call_radius.name}"
			      f"{', deletes something' if call_destroys else ''}")
			radius = max(radius, call_radius)
			destroys = destroys or call_destroys
		return radius, destroys

	async def route_tools(state: AgentState):
		next_node = tools_condition(state)
		# If no tools are invoked, return to the user
		if next_node == END:
			return END
		radius, destroys = await classify_pending_calls(state)
		if needs_confirmation(radius, impulse_threshold, destroys, destructive_threshold):
			threshold = destructive_threshold if destroys and radius < impulse_threshold else impulse_threshold
			print(f"Impulse radius {radius.name} reaches the threshold {threshold.name}, asking the user")
			return CONFIRM_TOOLS_NODE
		return AUTO_TOOLS_NODE

	workflow.add_conditional_edges(
		"agent", route_tools, [AUTO_TOOLS_NODE, CONFIRM_TOOLS_NODE, END]
	)
	workflow.add_edge(AUTO_TOOLS_NODE, "agent")
	workflow.add_edge(CONFIRM_TOOLS_NODE, "agent")

	# Now we can compile and visualize our graph
	graph = workflow.compile(
		checkpointer=checkpointer,
		interrupt_before=[CONFIRM_TOOLS_NODE],
		debug=False
	)

	return graph
