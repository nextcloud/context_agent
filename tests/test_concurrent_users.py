# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Concurrent agent runs must not share the user-scoped Nextcloud client, tools or task type.

Two react() calls for different users are interleaved on the event loop; every LLM sub-task
must be scheduled with the client, tools and task type of the run it belongs to.

Run standalone:

	PYTHONPATH=. python tests/test_concurrent_users.py

or via pytest when available.
"""
import asyncio
import json
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

from langchain_core.tools import tool  # noqa: E402
from nc_py_api import AsyncNextcloudApp  # noqa: E402

from ex_app.lib import agent  # noqa: E402
from ex_app.lib.all_tools.lib.impulse import ConfirmationPolicy  # noqa: E402
from ex_app.lib.nc_model import MULTIMODAL_INTERACTION, ChatWithNextcloud, Task  # noqa: E402


@tool
def alice_tool() -> str:
	"""A tool only alice has."""
	return "alice"


@tool
def bob_tool() -> str:
	"""A tool only bob has."""
	return "bob"


class UserApp(AsyncNextcloudApp):
	"""A client that remembers which user it was created for, without talking to a server."""

	def __init__(self, user: str):
		super().__init__()
		self.test_user = user


TOOLS = {"alice": [alice_tool], "bob": [bob_tool]}


def test_concurrent_react_keeps_users_apart():
	scheduled = []

	async def get_tools(nc):
		# Yield so that the other run gets to prepare its model in between
		await asyncio.sleep(0.01)
		return TOOLS[nc.test_user]

	async def get_confirmation_policy(nc):
		return ConfirmationPolicy()

	async def user_timezone_async(nc):
		return None

	async def schedule_task(self, task_input, prefer_streaming=False):
		await asyncio.sleep(0.01)
		scheduled.append({
			"user": self.nc.test_user,
			"input": task_input["input"],
			"tools": [t["function"]["name"] for t in json.loads(task_input["tools"])],
			"multimodal": self.multimodal,
		})
		return Task(id=len(scheduled), status="STATUS_SUCCESSFUL", output={"output": f"hello {self.nc.test_user}"})

	patches = [
		(agent, "get_tools", get_tools),
		(agent, "get_confirmation_policy", get_confirmation_policy),
		(agent, "user_timezone_async", user_timezone_async),
		(ChatWithNextcloud, "_schedule_task", schedule_task),
	]
	originals = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
	for obj, name, value in patches:
		setattr(obj, name, value)
	try:
		def task(user, task_type):
			return {
				"type": task_type,
				"input": {"input": f"question from {user}", "confirmation": 1, "conversation_token": ""},
			}

		async def run():
			return await asyncio.gather(
				agent.react(task("alice", MULTIMODAL_INTERACTION), UserApp("alice")),
				agent.react(task("bob", "core:contextagent:interaction"), UserApp("bob")),
			)

		alice_result, bob_result = asyncio.run(run())
	finally:
		for obj, name, value in originals:
			setattr(obj, name, value)

	assert alice_result["output"] == "hello alice"
	assert bob_result["output"] == "hello bob"
	assert len(scheduled) == 2
	for call in scheduled:
		user = call["user"]
		assert call["input"] == f"question from {user}", call
		assert call["tools"] == [f"{user}_tool"], call
		assert call["multimodal"] == (user == "alice"), call


def test_bind_tools_does_not_mutate_the_model():
	model = ChatWithNextcloud(nc=UserApp("alice"))
	bound = model.bind_tools([alice_tool])
	assert bound is not model
	assert model.tools == []
	assert bound.nc is model.nc
	assert [t["function"]["name"] for t in bound.tools] == ["alice_tool"]


def test_models_do_not_share_a_default_client():
	assert ChatWithNextcloud().nc is not ChatWithNextcloud().nc


if __name__ == "__main__":
	for name, fn in list(globals().items()):
		if name.startswith("test_") and callable(fn):
			fn()
			print(f"ok  {name}")
