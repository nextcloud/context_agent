# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fitting the conversation into the context window, see ex_app/lib/context_window.py"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("APP_ID", "context_agent")
os.environ.setdefault("APP_HOST", "127.0.0.1")
os.environ.setdefault("APP_PORT", "9081")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.setdefault("APP_VERSION", "2.9.1")
os.environ.setdefault("NEXTCLOUD_URL", "http://127.0.0.1:8080")
os.environ.setdefault("APP_PERSISTENT_STORAGE", tempfile.mkdtemp())
os.makedirs(os.environ["APP_PERSISTENT_STORAGE"], exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage  # noqa: E402
from langgraph.graph.message import REMOVE_ALL_MESSAGES  # noqa: E402
from nc_py_api import AsyncNextcloudApp  # noqa: E402

from ex_app.lib.context_window import (  # noqa: E402
    CHARS_PER_TOKEN,
    MIN_CONTEXT_BUDGET,
    OLD_TOOL_OUTPUT_MAX_LENGTH,
    message_size,
    render_transcript,
    select_context,
    split_turns,
    truncate_text,
)
from ex_app.lib.graph import get_graph  # noqa: E402
from ex_app.lib.jsonplus import JsonPlusSerializer  # noqa: E402
from ex_app.lib.memorysaver import MemorySaver  # noqa: E402


def tool_turn(n: int, output: str, calls: int = 1) -> list:
    """A turn in which the user asks, the model calls tools and answers"""
    call_ids = [f"c{n}-{i}" for i in range(calls)]
    return [
        HumanMessage(content=f"question {n}", id=f"h{n}"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"i": i}, "id": cid} for i, cid in enumerate(call_ids)], id=f"a{n}"),
        *[ToolMessage(content=output, name="lookup", tool_call_id=cid, id=f"t{cid}") for cid in call_ids],
        AIMessage(content=f"answer {n}", id=f"r{n}"),
    ]


def test_truncate_text_keeps_head_and_tail():
    text = "".join(f"line {i}\n" for i in range(2000))
    truncated = truncate_text(text, 1000, "lookup")
    assert len(truncated) <= 1000 + 50
    assert truncated.startswith("line 0\n")
    assert truncated.endswith("line 1999\n")
    assert "characters omitted; call lookup again" in truncated


def test_truncate_text_leaves_short_text_alone():
    assert truncate_text("short", 100) == "short"


def test_truncate_json_list_stays_valid():
    data = [{"id": i, "title": f"item {i}"} for i in range(500)]
    truncated = truncate_text(json.dumps(data, indent=2), 2000, "lookup")
    assert len(truncated) <= 2000
    parsed = json.loads(truncated)
    assert parsed[0] == data[0]
    assert parsed[-1] == data[-1]
    marker = next(item for item in parsed if isinstance(item, str))
    assert "items omitted" in marker


def test_split_turns():
    messages = tool_turn(1, "x") + tool_turn(2, "y")
    turns = split_turns(messages)
    assert [turn[0].id for turn in turns] == ["h1", "h2"]
    assert sum(len(turn) for turn in turns) == len(messages)


def test_current_turn_tool_outputs_are_not_truncated():
    # Many parallel calls: the first results are far more than 10 messages from the end
    big = "x" * (OLD_TOOL_OUTPUT_MAX_LENGTH * 3)
    messages = tool_turn(1, big * 5) + tool_turn(2, big, calls=12)
    # Room for the current turn, but not for the old result as well
    selection = select_context(messages, len(big) * 14 // CHARS_PER_TOKEN)
    assert selection.dropped == []
    assert selection.kept == messages
    old_tool, *current_tools = [m for m in selection.model_messages if m.type == "tool"]
    assert len(old_tool.content) <= OLD_TOOL_OUTPUT_MAX_LENGTH
    assert "omitted" in old_tool.content
    assert all(m.content == big for m in current_tools)


def test_old_tool_outputs_are_not_truncated_while_the_conversation_fits():
    big = "x" * (OLD_TOOL_OUTPUT_MAX_LENGTH * 3)
    messages = tool_turn(1, big) + tool_turn(2, big) + [HumanMessage(content="latest", id="latest")]
    selection = select_context(messages, 100_000)
    assert selection.dropped == []
    assert selection.model_messages == messages


def test_old_tool_outputs_are_truncated_oldest_first():
    big = "x" * (MIN_CONTEXT_BUDGET * CHARS_PER_TOKEN // 3)
    messages = tool_turn(1, big) + tool_turn(2, big) + tool_turn(3, big) + [HumanMessage(content="latest", id="latest")]
    # Room for two of the three results
    selection = select_context(messages, MIN_CONTEXT_BUDGET)
    assert selection.dropped == []
    assert selection.kept == messages
    oldest, *later = [m for m in selection.model_messages if m.type == "tool"]
    assert "omitted" in oldest.content
    assert all(m.content == big for m in later)
    assert sum(message_size(m) for m in selection.model_messages) <= MIN_CONTEXT_BUDGET * CHARS_PER_TOKEN


def test_old_turns_are_dropped_whole_when_over_budget():
    budget = MIN_CONTEXT_BUDGET
    messages = []
    for n in range(30):
        messages += tool_turn(n, "y" * 1500)
    selection = select_context(messages, budget)
    assert selection.dropped
    assert selection.dropped + selection.kept == messages
    assert selection.kept[0].type == "human"
    # Trimmed below the budget, leaving room for the next turns
    assert sum(len(json.dumps(m.content)) for m in selection.model_messages) < budget * CHARS_PER_TOKEN
    # The current turn is always kept
    assert selection.kept[-len(tool_turn(0, "")):][0].id == "h29"


def test_current_turn_alone_over_budget():
    budget = MIN_CONTEXT_BUDGET
    budget_chars = budget * CHARS_PER_TOKEN
    huge = "z" * (budget_chars * 2)
    messages = [
        # Long enough that halving the two results alone does not make the turn fit
        HumanMessage(content="do it " + "d" * 1000, id="h"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "c1"}], id="a1"),
        ToolMessage(content=huge, name="lookup", tool_call_id="c1", id="t1"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "c2"}], id="a2"),
        ToolMessage(content=huge, name="lookup", tool_call_id="c2", id="t2"),
    ]
    selection = select_context(messages, budget)
    assert selection.dropped == []
    earlier, latest = [m for m in selection.model_messages if m.type == "tool"]
    # The result the model has not seen yet gets the most room, but no more than half the budget
    assert OLD_TOOL_OUTPUT_MAX_LENGTH < len(latest.content) <= budget_chars // 2 + 200
    assert len(earlier.content) <= OLD_TOOL_OUTPUT_MAX_LENGTH + 200
    # The state keeps the full results
    assert selection.kept == messages


def test_pending_tool_results_share_the_budget():
    budget = MIN_CONTEXT_BUDGET
    budget_chars = budget * CHARS_PER_TOKEN
    big = "z" * budget_chars
    call_ids = [f"c{i}" for i in range(3)]
    messages = [
        HumanMessage(content="do it", id="h"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": cid} for cid in call_ids + ["small"]], id="a"),
        *[ToolMessage(content=big, name="lookup", tool_call_id=cid, id=f"t{cid}") for cid in call_ids],
        ToolMessage(content="tiny", name="lookup", tool_call_id="small", id="tsmall"),
    ]
    selection = select_context(messages, budget)
    *large, small = [m for m in selection.model_messages if m.type == "tool"]
    # Together the results fit, and the room the small one does not need goes to the large ones
    assert sum(len(json.dumps(m.content)) for m in selection.model_messages) <= budget_chars
    assert small.content == "tiny"
    assert all(len(m.content) > budget_chars // 4 for m in large)
    assert selection.kept == messages


def test_pending_tool_results_are_omitted_rather_than_exceeding_the_budget():
    budget = MIN_CONTEXT_BUDGET
    budget_chars = budget * CHARS_PER_TOKEN
    # Escaped characters make the results larger than their text
    big = '"z"\n' * budget_chars
    call_ids = [f"c{i}" for i in range(12)]
    messages = [
        HumanMessage(content="do it", id="h"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": cid} for cid in call_ids], id="a"),
        *[ToolMessage(content=big, name="lookup", tool_call_id=cid, id=f"t{cid}") for cid in call_ids],
    ]
    selection = select_context(messages, budget)
    assert sum(message_size(m) for m in selection.model_messages) <= budget_chars
    tools = [m for m in selection.model_messages if m.type == "tool"]
    # Every tool call still has its result, the oldest ones without their content
    assert [m.tool_call_id for m in tools] == call_ids
    assert "result omitted" in tools[0].content
    assert "result omitted" not in tools[-1].content
    assert len(tools[-1].content) > OLD_TOOL_OUTPUT_MAX_LENGTH // 4
    assert selection.kept == messages


def test_earlier_tool_results_are_omitted_before_pending_ones():
    budget = MIN_CONTEXT_BUDGET
    budget_chars = budget * CHARS_PER_TOKEN
    big = "z" * budget_chars
    messages = [HumanMessage(content="do it", id="h")]
    for n in range(8):
        messages += [
            AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": f"c{n}"}], id=f"a{n}"),
            ToolMessage(content=big, name="lookup", tool_call_id=f"c{n}", id=f"t{n}"),
        ]
    selection = select_context(messages, budget)
    assert sum(message_size(m) for m in selection.model_messages) <= budget_chars
    tools = [m for m in selection.model_messages if m.type == "tool"]
    assert len(tools) == 8
    assert "result omitted" in tools[0].content
    assert "call lookup again" in tools[0].content
    # The result the model has not seen yet is still shown
    assert "result omitted" not in tools[-1].content
    assert len(tools[-1].content) > OLD_TOOL_OUTPUT_MAX_LENGTH // 2
    assert selection.kept == messages


def test_current_turn_marker_does_not_suggest_calling_again():
    budget = MIN_CONTEXT_BUDGET
    huge = "z" * (budget * CHARS_PER_TOKEN * 2)
    messages = tool_turn(1, huge) + tool_turn(2, huge)[:-1]
    selection = select_context(messages, budget)
    old, current = [m for m in selection.model_messages if m.type == "tool"]
    # Calling the tool again gets an old result back in full, but not one of the current turn
    assert "call lookup again" in old.content
    assert "call lookup again" not in current.content
    assert "too large to be shown in full" in current.content


def test_render_transcript():
    transcript = render_transcript(tool_turn(1, "result " * 1000), 100_000)
    assert "User: question 1" in transcript
    assert 'Assistant called the tool lookup with {"i": 0}' in transcript
    assert "Result of lookup:" in transcript
    assert "Assistant: answer 1" in transcript
    assert len(transcript) < 3000


def test_graph_replaces_dropped_messages_and_keeps_summary():
    """The update call_model returns once it has summarized the dropped turns"""
    # Without the final answer, which call_model is about to give
    messages = (tool_turn(1, "a") + tool_turn(2, "b"))[:-1]
    kept = tool_turn(2, "b")[:-1]

    async def call_model(state, config):
        if len(state["messages"]) == len(messages):
            return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept, AIMessage(content="done")], "summary": "earlier: 1"}
        return {"messages": [AIMessage(content="again")]}

    async def run():
        checkpointer = MemorySaver(serde=JsonPlusSerializer())
        graph, _ = await get_graph(call_model, [], checkpointer)
        config = {"configurable": {"thread_id": "t"}}
        await graph.ainvoke({"messages": messages}, config)
        state = graph.get_state(config).values
        assert state["summary"] == "earlier: 1"
        assert [m.id for m in state["messages"][:-1]] == [m.id for m in kept]
        assert state["messages"][-1].content == "done"
        # The summary stays when later steps do not touch it
        await graph.ainvoke({"messages": [HumanMessage(content="more")]}, config)
        assert graph.get_state(config).values["summary"] == "earlier: 1"

    asyncio.run(run())


def test_react_summarizes_dropped_turns_without_streaming_the_summary():
    from ex_app.lib import agent
    from ex_app.lib.all_tools.lib.impulse import ConfirmationPolicy
    from ex_app.lib.nc_model import ChatWithNextcloud, Task, extract_text_content

    scheduled = []

    async def get_tools(nc):
        return []

    async def get_confirmation_policy(nc):
        return ConfirmationPolicy()

    async def get_context_budget(nc):
        return MIN_CONTEXT_BUDGET

    async def user_timezone_async(nc):
        return None

    async def schedule_task(self, task_input, prefer_streaming=False):
        scheduled.append(task_input)
        if task_input["system_prompt"].startswith("You summarize"):
            output = "SUMMARY-TEXT"
        else:
            output = "final answer"
        return Task(id=len(scheduled), status="STATUS_SUCCESSFUL", output={"output": output})

    async def answer(state, config):
        return {"messages": [AIMessage(content="ok " + "w" * 1500)]}

    async def build_token():
        checkpointer = MemorySaver(serde=JsonPlusSerializer())
        graph, _ = await get_graph(answer, [], checkpointer)
        config = {"configurable": {"thread_id": "thread-1"}}
        for n in range(20):
            await graph.ainvoke({"messages": [HumanMessage(content=f"question {n} " + "q" * 1500)]}, config)
        return agent.export_conversation(checkpointer)

    streamed = []

    async def stream_output(state):
        streamed.append(state["output"])

    patches = [
        (agent, "get_tools", get_tools),
        (agent, "get_confirmation_policy", get_confirmation_policy),
        (agent, "get_context_budget", get_context_budget),
        (agent, "user_timezone_async", user_timezone_async),
        (ChatWithNextcloud, "_schedule_task", schedule_task),
    ]
    originals = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
    for obj, name, value in patches:
        setattr(obj, name, value)
    try:
        token = asyncio.run(build_token())
        task = {
            "type": "core:contextagent:interaction",
            "preferStreaming": True,
            "input": {"input": "latest question", "confirmation": 1, "conversation_token": token},
        }
        result = asyncio.run(agent.react(task, AsyncNextcloudApp(), stream_output))
    finally:
        for obj, name, value in originals:
            setattr(obj, name, value)

    summary_calls = [t for t in scheduled if t["system_prompt"].startswith("You summarize")]
    model_calls = [t for t in scheduled if t not in summary_calls]
    assert len(summary_calls) == 1
    assert "question 0" in summary_calls[0]["input"]
    assert len(model_calls) == 1
    # The summary leads the history and is kept out of the instructions
    assert "SUMMARY-TEXT" not in model_calls[0]["system_prompt"]
    leading = json.loads(model_calls[0]["history"][0])
    assert leading["role"] == "human"
    assert leading["content"] == agent.SUMMARY_MESSAGE_PREFIX + "SUMMARY-TEXT"
    assert not any("SUMMARY-TEXT" in entry for entry in model_calls[0]["history"][1:])
    assert model_calls[0]["input"] == "latest question"
    assert len(model_calls[0]["history"]) < 40
    assert result["output"] == "final answer"
    assert not any("SUMMARY-TEXT" in output for output in streamed)

    async def load_state():
        graph, _ = await get_graph(answer, [], agent.load_conversation(result["conversation_token"]))
        return graph.get_state({"configurable": {"thread_id": "thread-1"}}).values

    state = asyncio.run(load_state())
    assert state["summary"] == "SUMMARY-TEXT"
    assert state["messages"][0].type == "human"
    # The summary message is not kept in the state: the history without it, the latest question and the answer
    assert not any("SUMMARY-TEXT" in extract_text_content(m.content) for m in state["messages"])
    assert len(state["messages"]) == len(model_calls[0]["history"]) - 1 + 2


def test_reserved_size_comes_off_the_budget():
    budget = 3 * MIN_CONTEXT_BUDGET
    messages = []
    for n in range(12):
        messages += tool_turn(n, "x" * 1500)
    messages.append(HumanMessage(content="latest", id="latest"))
    assert not select_context(messages, budget).dropped

    reserved = MIN_CONTEXT_BUDGET * CHARS_PER_TOKEN
    selection = select_context(messages, budget, reserved)
    assert selection.dropped
    assert sum(message_size(m) for m in selection.model_messages) + reserved <= budget * CHARS_PER_TOKEN


def test_conversation_keeps_a_minimum_when_the_rest_takes_up_the_budget():
    budget = 3 * MIN_CONTEXT_BUDGET
    messages = tool_turn(0, "x" * 1500) + [HumanMessage(content="latest", id="latest")]
    selection = select_context(messages, budget, 10 * budget * CHARS_PER_TOKEN)
    assert not selection.dropped
    assert selection.model_messages == messages


def test_react_counts_instructions_and_tools_towards_the_budget():
    from langchain_core.tools import tool

    from ex_app.lib import agent
    from ex_app.lib.all_tools.lib.impulse import ConfirmationPolicy
    from ex_app.lib.nc_model import ChatWithNextcloud, Task

    budget = 4 * MIN_CONTEXT_BUDGET
    scheduled = []
    logged = []

    @tool
    def big_tool() -> str:
        """Placeholder"""
        return ""

    # As large as half the budget
    big_tool.description = "d" * (2 * MIN_CONTEXT_BUDGET * CHARS_PER_TOKEN)

    async def get_confirmation_policy(nc):
        return ConfirmationPolicy()

    async def get_context_budget(nc):
        return budget

    async def user_timezone_async(nc):
        return None

    async def log(nc, level, content):
        logged.append(str(content))

    async def schedule_task(self, task_input, prefer_streaming=False):
        scheduled.append(task_input)
        # The summarizer ignores the length it is asked for
        output = "S" * (budget * CHARS_PER_TOKEN) if task_input["system_prompt"].startswith("You summarize") else "final answer"
        return Task(id=len(scheduled), status="STATUS_SUCCESSFUL", output={"output": output})

    async def answer(state, config):
        return {"messages": [AIMessage(content="ok " + "w" * 1500)]}

    async def build_token():
        checkpointer = MemorySaver(serde=JsonPlusSerializer())
        graph, _ = await get_graph(answer, [], checkpointer)
        config = {"configurable": {"thread_id": "thread-1"}}
        # About half of the budget, so it fits unless the tools are counted
        for n in range(5):
            await graph.ainvoke({"messages": [HumanMessage(content=f"question {n} " + "q" * 1500)]}, config)
        return agent.export_conversation(checkpointer)

    def run(tools):
        scheduled.clear()
        logged.clear()

        async def get_tools(nc):
            return tools

        patches = [
            (agent, "get_tools", get_tools),
            (agent, "get_confirmation_policy", get_confirmation_policy),
            (agent, "get_context_budget", get_context_budget),
            (agent, "user_timezone_async", user_timezone_async),
            (agent, "log", log),
            (ChatWithNextcloud, "_schedule_task", schedule_task),
        ]
        originals = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
        for obj, name, value in patches:
            setattr(obj, name, value)
        try:
            token = asyncio.run(build_token())
            task = {
                "type": "core:contextagent:interaction",
                "input": {"input": "latest question", "confirmation": 1, "conversation_token": token},
            }
            asyncio.run(agent.react(task, AsyncNextcloudApp()))
        finally:
            for obj, name, value in originals:
                setattr(obj, name, value)
        return [t for t in scheduled if t["system_prompt"].startswith("You summarize")]

    assert not run([])
    assert not any("context budget" in line for line in logged)

    assert len(run([big_tool])) == 1
    request = scheduled[-1]
    # The new summary is sent along and counts as well
    assert json.loads(request["history"][0])["content"].startswith(agent.SUMMARY_MESSAGE_PREFIX + "S")
    size = len(request["system_prompt"]) + len(request["tools"]) + sum(len(entry) for entry in request["history"]) + len(request["input"])
    assert size <= budget * CHARS_PER_TOKEN
    assert not any("context budget" in line for line in logged)

    # Tools that leave less than the minimum for the conversation are reported
    big_tool.description = "d" * (3 * MIN_CONTEXT_BUDGET * CHARS_PER_TOKEN)
    run([big_tool])
    assert any("context budget" in line for line in logged)
