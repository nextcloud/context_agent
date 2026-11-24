# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Created Scheduled Tasks are reported to the client that scheduled the agent run.

The ``create_scheduled_task`` tool returns the scheduled task id under the ``scheduled_task_id``
key of a JSON result, which langgraph's tool node stores as the ``ToolMessage`` content. The
agent copies these ids from the structured tool results of the run -- never from the model's
final text -- into the task output under the ``scheduled_task_ids`` key, so the assistant's
listener can stamp the requester on the assignment.

Run standalone:

	PYTHONPATH=. python tests/test_created_task_ids.py

or via pytest when available.
"""
import os
import sys
import tempfile

os.environ.setdefault("APP_ID", "context_agent")
os.environ.setdefault("APP_HOST", "127.0.0.1")
os.environ.setdefault("APP_PORT", "9081")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.setdefault("APP_VERSION", "2.10.0")
os.environ.setdefault("NEXTCLOUD_URL", "http://127.0.0.1:8080")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# agent.py stores its signing key in the persistent storage on import
os.environ.setdefault("APP_PERSISTENT_STORAGE", tempfile.mkdtemp())

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from ex_app.lib.agent import collect_created_scheduled_task_ids  # noqa: E402


def _tool_result(scheduled_task_id: int, message_id: str) -> ToolMessage:
	# langgraph's tool node JSON-encodes a dict tool result into the content
	result = ToolMessage(content=f'{{"scheduled_task_id": {scheduled_task_id}}}', tool_call_id=f"call_{message_id}")
	result.id = message_id
	return result


def test_collects_the_ids_of_this_runs_tool_results_in_order():
	previous = AIMessage(content="I will create two tasks.", id="old-ai")
	older_result = _tool_result(1, "old-result")
	previous_message_ids = {previous.id, older_result.id}
	messages = [
		previous,
		older_result,
		_tool_result(7, "result-1"),
		_tool_result(9, "result-2"),
		AIMessage(content="Both tasks are created.", id="new-ai"),
	]
	assert collect_created_scheduled_task_ids(messages, previous_message_ids) == [7, 9]


def test_ignores_tool_results_of_earlier_runs():
	earlier_result = _tool_result(3, "earlier-result")
	assert collect_created_scheduled_task_ids([earlier_result], {earlier_result.id}) == []


def test_ignores_results_without_an_assignment_id():
	other_tool = ToolMessage(content='{"assignments": []}', tool_call_id="call_list")
	other_tool.id = "list-result"
	assert collect_created_scheduled_task_ids([other_tool], set()) == []


def test_tolerates_errors_and_missing_ids():
	failed = ToolMessage(content="Error: boom\n please fix your mistakes.", tool_call_id="call_err")
	failed.id = "err-result"
	created_without_id = ToolMessage(content='{"assignment_id": null}', tool_call_id="call_noid")
	created_without_id.id = "noid-result"
	assert collect_created_scheduled_task_ids([failed, created_without_id], set()) == []


if __name__ == "__main__":
	for name, fn in list(globals().items()):
		if name.startswith("test_") and callable(fn):
			fn()
			print(f"ok  {name}")
