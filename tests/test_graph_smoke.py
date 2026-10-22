# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression smoke for the LangChain 1.x / langgraph 1.2.14 / checkpoint 4.x migration.

Exercises the three vendored coupling points without network access:
- ex_app.lib.nc_model.ChatWithNextcloud (custom BaseChatModel on langchain-core 1.x)
- ex_app.lib.memorysaver.InMemorySaver (vendored checkpointer on langgraph-checkpoint 4.x)
- ex_app.lib.jsonplus.JsonPlusSerializer (custom serde, incl. the signed-token shape)

Runnable standalone (``python tests/test_graph_smoke.py``) or via pytest.
"""
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ID", "context_agent")
os.environ.setdefault("APP_HOST", "127.0.0.1")
os.environ.setdefault("APP_PORT", "9081")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.setdefault("APP_VERSION", "2.9.1")
os.environ.setdefault("NEXTCLOUD_URL", "http://127.0.0.1:8080")
os.environ.setdefault("APP_PERSISTENT_STORAGE", str(Path(__file__).parent / ".tmp_persistent"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from ex_app.lib.graph import get_graph  # noqa: E402
from ex_app.lib.jsonplus import JsonPlusSerializer  # noqa: E402
from ex_app.lib.memorysaver import InMemorySaver  # noqa: E402
from ex_app.lib.nc_model import ChatWithNextcloud, Response  # noqa: E402


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"sunny in {city}"


def test_convert_to_openai_tool_replug():
    model = ChatWithNextcloud()
    bound = model.bind_tools([get_weather])
    assert bound is model
    assert len(model.tools) == 1
    converted = model.tools[0]
    assert converted["type"] == "function"
    assert converted["function"]["name"] == "get_weather"


def test_build_task_input_and_task_to_message():
    model = ChatWithNextcloud()
    model.tools = [{"type": "function", "function": {"name": "get_weather"}}]
    task_input = model._build_task_input(
        [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "get_weather", "args": {"city": "x"}, "id": "c1"}]),
            ToolMessage(content="sunny", name="get_weather", tool_call_id="c1"),
            HumanMessage(content="and tomorrow?"),
        ]
    )
    assert task_input["system_prompt"] == "sys"
    assert task_input["input"] == "and tomorrow?"
    assert len(task_input["history"]) == 3

    message = model._task_to_message(
        Response.model_validate(
            {
                "task": {
                    "id": 1,
                    "status": "STATUS_SUCCESSFUL",
                    "output": {
                        "output": "it is sunny",
                        "tool_calls": [{"name": "get_weather", "args": {"city": "x"}, "id": "c1"}],
                    },
                }
            }
        ).task
    )
    assert isinstance(message, AIMessage)
    assert message.content == "it is sunny"
    assert message.tool_calls[0]["name"] == "get_weather"


def test_serializer_roundtrip_token_shape():
    serde = JsonPlusSerializer()
    state = {
        "last_config": {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "ckpt1"}},
        "last_checkpoint": ("msgpack", serde.dumps_typed({"v": 4, "id": "ckpt1"})[1]),
    }
    token = serde.dumps(state)
    restored = serde.loads(token)
    assert restored["last_config"]["configurable"]["checkpoint_id"] == "ckpt1"
    assert bytes(restored["last_checkpoint"][1]) == state["last_checkpoint"][1]

    typed = serde.dumps_typed({"messages": [HumanMessage("hello"), AIMessage("hi")]})
    kind, blob = typed
    assert kind == "msgpack"
    loaded = serde.loads_typed((kind, blob))
    assert isinstance(loaded["messages"][0], HumanMessage)
    assert isinstance(loaded["messages"][1], AIMessage)


def test_stategraph_step_with_vendored_checkpointer():
    calls = {"n": 0}
    real_agenerate = ChatWithNextcloud._agenerate

    async def fake_agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        calls["n"] += 1
        if calls["n"] == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "get_weather", "args": {"city": "Karlsruhe"}, "id": "c1"}],
            )
        else:
            message = AIMessage(content="It is sunny in Karlsruhe.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    ChatWithNextcloud._agenerate = fake_agenerate
    try:
        model = ChatWithNextcloud()
        bound = model.bind_tools([get_weather])

        async def call_model(state, config):
            response = await bound.ainvoke([SystemMessage(content="sys")] + list(state["messages"]), config)
            return {"messages": [response]}

        checkpointer = InMemorySaver()

        async def run():
            graph, _classifications = await get_graph(call_model, tools=[get_weather], checkpointer=checkpointer)
            thread = {"configurable": {"thread_id": "thread-smoke"}}

            async def stream(resume=None):
                events = []
                async for event in graph.astream(resume, thread, stream_mode="values"):
                    events.append(event)
                return events

            await stream({"messages": [HumanMessage(content="weather in Karlsruhe?")]})
            snapshot = graph.get_state(thread)
            assert snapshot.next == ("dangerous_tools",), f"expected interrupt, got {snapshot.next}"
            assert snapshot.values["messages"][-1].tool_calls[0]["name"] == "get_weather"
            assert checkpointer.last_config is not None
            assert len(checkpointer.storage["thread-smoke"][""]) >= 1

            await stream(None)
            snapshot = graph.get_state(thread)
            assert snapshot.next == ()
            final = snapshot.values["messages"][-1]
            assert isinstance(final, AIMessage)
            assert final.content == "It is sunny in Karlsruhe."
            tool_messages = [m for m in snapshot.values["messages"] if isinstance(m, ToolMessage)]
            assert tool_messages and tool_messages[0].content == "sunny in Karlsruhe"

        asyncio.run(run())
    finally:
        ChatWithNextcloud._agenerate = real_agenerate


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    raise SystemExit(1 if failures else 0)
