# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compatibility regression for conversation tokens created before the LangChain 1.x migration.

The tokens in ``tests/fixtures/`` were signed and serialized by a pre-migration
instance (langgraph 1.0.1 / langgraph-checkpoint 3.0.1 / langchain-core 0.3.86,
commit 2b417d6). Users keep such tokens inside their conversation history, so
``load_conversation`` (last-checkpoint shape) and ``load_conversation_old``
(whole-storage legacy shape) must keep reading them after every langgraph /
langchain / serde update. If these tests fail after a dependency bump, the bump
breaks existing conversations.

Runnable standalone (``python tests/test_conversation_token_compat.py``) or via pytest.
"""
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
os.makedirs(os.environ["APP_PERSISTENT_STORAGE"], exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ex_app.lib.agent as agent  # noqa: E402
from ex_app.lib.memorysaver import MemorySaver  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

# the fixture tokens were signed with this key (see the generator script in
# the commit that added them); the instance key is replaced with it here
FIXTURE_KEY = "context-agent-compat-fixture-key-v1"
FIXTURES = Path(__file__).parent / "fixtures"
THREAD_ID = "conv-compat-1"


def _load_fixture(name: str) -> str:
	return (FIXTURES / name).read_text().rstrip("\n")


def _use_fixture_key(monkeypatch) -> None:
	if monkeypatch is not None:
		monkeypatch.setattr(agent, "key", FIXTURE_KEY)
	else:
		agent.key = FIXTURE_KEY


def _assert_conversation_readable(checkpointer: MemorySaver) -> None:
	"""Read the restored state through the checkpointer, like the agent graph does."""
	assert THREAD_ID in checkpointer.storage
	namespace = checkpointer.storage[THREAD_ID]
	checkpoint_id = next(iter(namespace[""]))
	tuple_ = checkpointer.get_tuple(
		{"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": "", "checkpoint_id": checkpoint_id}}
	)
	assert tuple_ is not None
	messages = tuple_.checkpoint["channel_values"]["messages"]

	assert isinstance(messages[0], HumanMessage)
	assert messages[0].content == "What's the weather in Karlsruhe?"

	assert isinstance(messages[1], AIMessage)
	assert messages[1].tool_calls[0]["name"] == "get_weather"
	assert messages[1].tool_calls[0]["args"] == {"city": "Karlsruhe"}
	assert messages[1].tool_calls[0]["id"] == "call-1"

	assert isinstance(messages[2], ToolMessage)
	assert messages[2].content == "sunny, 18C"
	assert messages[2].tool_call_id == "call-1"

	assert isinstance(messages[3], AIMessage)
	assert messages[3].content == "It is sunny in Karlsruhe, around 18 degrees."


def test_pre_migration_new_style_token_loads(monkeypatch=None):
	_use_fixture_key(monkeypatch)
	token = _load_fixture("conversation_token_pre_langchain_1x_new_style.txt")
	checkpointer = agent.load_conversation(token)
	assert isinstance(checkpointer, MemorySaver)
	_assert_conversation_readable(checkpointer)


def test_pre_migration_legacy_whole_storage_token_loads(monkeypatch=None):
	_use_fixture_key(monkeypatch)
	token = _load_fixture("conversation_token_pre_langchain_1x_legacy_whole_storage.txt")
	checkpointer = agent.load_conversation_old(token)
	assert isinstance(checkpointer, MemorySaver)
	_assert_conversation_readable(checkpointer)


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
