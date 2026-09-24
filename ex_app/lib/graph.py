# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import traceback
from typing import Sequence

from langchain_core.messages import ToolMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict, Annotated

from ex_app.lib.all_tools.lib.impulse import (
	DEFAULT_DESTRUCTIVE_THRESHOLD,
	DEFAULT_IMPULSE_THRESHOLD,
	ImpulseRadius,
	classify_destructive,
	classify_tool_call,
	is_always_confirmed,
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


class PendingClassification:
	"""What the router last worked out about a batch of tool calls.

	The radius and destructiveness of a call are settled while routing it, before
	the run stops to ask the user about it. Keeping them here lets the caller
	report how far the calls it is asking about reach without running the hooks --
	several of which query the Nextcloud API -- a second time.

	The record is tied to the message holding the calls: a run that ends anywhere
	other than :data:`CONFIRM_TOOLS_NODE` leaves the previous turn's record behind,
	and matching on the message keeps that from being read as this turn's answer.
	"""

	def __init__(self):
		self._recorded = False
		self._message_id = None
		self._radius = ImpulseRadius.SELF
		self._destroys = False

	def record(self, message: BaseMessage, radius: ImpulseRadius, destroys: bool) -> None:
		self._recorded = True
		self._message_id = getattr(message, 'id', None)
		self._radius = radius
		self._destroys = destroys

	def of(self, message: BaseMessage) -> tuple[ImpulseRadius, bool] | None:
		"""The classification of this message's tool calls, or None if no routing
		decision was recorded for this message."""
		if not self._recorded or getattr(message, 'id', None) != self._message_id:
			return None
		return self._radius, self._destroys


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
) -> tuple[CompiledStateGraph, PendingClassification]:
	"""Build the agent graph, along with the :class:`PendingClassification` that its
	routing writes to.
	"""
	tools_by_name = {tool.name: tool for tool in tools}
	pending = PendingClassification()

	# Define a new graph
	workflow = StateGraph(AgentState)

	# Define the two nodes we will cycle between
	workflow.add_node("agent", call_model)
	workflow.add_node(AUTO_TOOLS_NODE, create_tool_node_with_fallback(tools))
	workflow.add_node(CONFIRM_TOOLS_NODE, create_tool_node_with_fallback(tools))

	# Set the entrypoint as `agent`
	# This means that this node is the first one called
	workflow.set_entry_point("agent")

	async def classify_one(tool_call) -> tuple[ImpulseRadius, bool, bool] | None:
		"""Classify a single pending call, or None if there is nothing to classify.

		Both hooks hit the network, so run them against each other rather than one
		after the other.
		"""
		tool = tools_by_name.get(tool_call["name"])
		if tool is None:
			# The model named a tool that does not exist. Neither node can run it --
			# both hold the same list -- so the call reaches nobody, and the node's
			# error handler hands the model its mistake to correct on the next turn.
			# Asking the user to confirm it would be asking about something that
			# cannot happen, so it is left out of the reckoning entirely.
			return None
		call_args = tool_call.get("args") or {}
		call_radius, call_destroys = await asyncio.gather(
			classify_tool_call(tool, call_args),
			classify_destructive(tool, call_args),
		)
		return call_radius, call_destroys, is_always_confirmed(tool)

	async def classify_pending_calls(state: AgentState) -> tuple[ImpulseRadius, bool, bool]:
		"""The widest radius the pending tool calls reach, whether any destroys
		something, and whether any is confirmed regardless of its radius.

		The calls are classified against each other -- a batch of them would
		otherwise pay for every lookup in series before any tool runs -- and folded
		together afterwards, so what gets logged stays in the model's order.
		"""
		tool_calls = state["messages"][-1].tool_calls
		classified = await asyncio.gather(*(classify_one(tc) for tc in tool_calls))

		radius = ImpulseRadius.SELF
		destroys = False
		always = False
		for tool_call, classification in zip(tool_calls, classified):
			if classification is None:
				print(f"Tool call: {tool_call['name']} -> no such tool, leaving it to the tool node to report")
				continue
			call_radius, call_destroys, call_always = classification
			print(f"Tool call: {tool_call['name']} -> impulse radius {call_radius.name}"
			      f"{', destroys something' if call_destroys else ''}"
			      f"{', always confirmed' if call_always else ''}")
			radius = max(radius, call_radius)
			destroys = destroys or call_destroys
			always = always or call_always
		return radius, destroys, always

	async def route_tools(state: AgentState):
		next_node = tools_condition(state)
		# If no tools are invoked, return to the user
		if next_node == END:
			return END
		radius, destroys, always = await classify_pending_calls(state)
		pending.record(state["messages"][-1], radius, destroys)
		if needs_confirmation(radius, impulse_threshold, destroys, destructive_threshold, always):
			if always:
				print("A pending tool call is always confirmed, asking the user")
			else:
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

	return graph, pending
