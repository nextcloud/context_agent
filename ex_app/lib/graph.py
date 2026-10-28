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
	CallClassification,
	ConfirmationPolicy,
	classify_destructive,
	classify_item_count,
	classify_reversibility,
	classify_tool_call,
	is_always_confirmed,
)

# The two tool nodes hold the same tools; only one of them is interrupted before.
# Which one a batch of calls is routed to is decided by the ConfirmationPolicy.
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


class CallClassifications:
	"""What the router worked out about each tool call it has routed.

	Every dimension of a call is settled while routing it, before
	the run stops to ask the user about it. Keeping them here lets the caller
	describe the calls it is asking about without running the hooks -- several of
	which query the Nextcloud API -- a second time.

	Records are keyed by tool call id, which is unique to a call, so a record left
	behind by an earlier turn can never be read as this turn's answer.
	"""

	def __init__(self, policy: ConfirmationPolicy):
		self._policy = policy
		self._by_call_id: dict[str, CallClassification] = {}

	def record(self, tool_call, classification: CallClassification) -> None:
		call_id = tool_call.get('id')
		if call_id is None:
			return
		self._by_call_id[call_id] = classification

	def annotate(self, tool_calls) -> list[dict]:
		"""The tool calls as they go out to the user, each carrying how far it
		reaches, whether it takes something away, whether it can be taken back and
		how many items it touches.

		Counting stops once the count limit is reached, so ``item_count`` is a lower
		bound unless ``item_count_exact`` says otherwise; it is None when nobody can
		tell.

		A call no record was kept for -- a tool name the model made up, which no
		node can run -- is passed on as it came in rather than guessed at.
		"""
		annotated = []
		for tool_call in tool_calls:
			call = dict(tool_call)
			classification = self._by_call_id.get(tool_call.get('id'))
			if classification is not None:
				call['impulse_radius'] = classification.radius.name.lower()
				call['destructive'] = classification.destroys
				call['reversibility'] = classification.reversibility.name.lower()
				call['item_count'] = classification.item_count
				call['item_count_exact'] = (
					classification.item_count is not None
					and classification.item_count < self._policy.count_limit
				)
			annotated.append(call)
		return annotated


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
	policy: ConfirmationPolicy = ConfirmationPolicy(),
) -> tuple[CompiledStateGraph, CallClassifications]:
	"""Build the agent graph, along with the :class:`CallClassifications` that its
	routing writes to.
	"""
	tools_by_name = {tool.name: tool for tool in tools}
	classifications = CallClassifications(policy)

	# Define a new graph
	workflow = StateGraph(AgentState)

	# Define the two nodes we will cycle between
	workflow.add_node("agent", call_model)
	workflow.add_node(AUTO_TOOLS_NODE, create_tool_node_with_fallback(tools))
	workflow.add_node(CONFIRM_TOOLS_NODE, create_tool_node_with_fallback(tools))

	# Set the entrypoint as `agent`
	# This means that this node is the first one called
	workflow.set_entry_point("agent")

	async def classify_one(tool_call) -> CallClassification | None:
		"""Classify a single pending call, or None if there is nothing to classify.

		The hooks may hit the network, so run them against each other rather than
		one after the other.
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
		radius, destroys, reversibility, item_count = await asyncio.gather(
			classify_tool_call(tool, call_args),
			classify_destructive(tool, call_args),
			classify_reversibility(tool, call_args),
			classify_item_count(tool, call_args, policy.count_limit),
		)
		return CallClassification(radius, destroys, reversibility, item_count, is_always_confirmed(tool))

	async def classify_pending_calls(state: AgentState) -> list[CallClassification]:
		"""What the hooks make of each pending tool call.

		The calls are classified against each other -- a batch of them would
		otherwise pay for every lookup in series before any tool runs -- and logged
		and recorded afterwards, so that stays in the model's order.
		"""
		tool_calls = state["messages"][-1].tool_calls
		classified = await asyncio.gather(*(classify_one(tc) for tc in tool_calls))

		calls = []
		for tool_call, classification in zip(tool_calls, classified):
			if classification is None:
				print(f"Tool call: {tool_call['name']} -> no such tool, leaving it to the tool node to report")
				continue
			item_count = '?' if classification.item_count is None else classification.item_count
			print(f"Tool call: {tool_call['name']} -> impulse radius {classification.radius.name}"
			      f", {classification.reversibility.name.lower()}, {item_count} items"
			      f"{', destroys something' if classification.destroys else ''}"
			      f"{', always confirmed' if classification.always else ''}")
			classifications.record(tool_call, classification)
			calls.append(classification)
		return calls

	async def route_tools(state: AgentState):
		next_node = tools_condition(state)
		# If no tools are invoked, return to the user
		if next_node == END:
			return END
		reasons = policy.reasons(await classify_pending_calls(state))
		if reasons:
			print(f"Asking the user: {'; '.join(reasons)}")
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

	return graph, classifications
