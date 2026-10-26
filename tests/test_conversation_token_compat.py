# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conversation tokens handed out by every tagged release must still be readable.

The clients keep the conversation_token of a conversation and send it back with
the next message, possibly long after an app update. tests/fixtures/conversation_tokens
holds tokens minted by the code and dependencies of each release tag (see mint_all.sh
there), for these conversations:

- answer: a plain question and answer
- safe_tool: a tool call that ran without confirmation
- second_turn: a second question, asked after loading the token of `answer`
- pending_confirmation: a tool call waiting for the user to confirm it
- confirmed / denied: `pending_confirmation` after the user confirmed / denied it

Each token has to load into the conversation it was minted from, the conversation
has to go on from where it stopped, and the token of that has to load again.
The same goes for tokens minted by the current code (as tag HEAD), which also makes
sure mint.py still works for the next release.

Run standalone:

	PYTHONPATH=. python tests/test_conversation_token_compat.py

or via pytest when available.
"""
import asyncio
import glob
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APP_ID", "context_agent")
os.environ.setdefault("APP_HOST", "127.0.0.1")
os.environ.setdefault("APP_PORT", "9081")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ.setdefault("APP_VERSION", "2.9.1")
os.environ.setdefault("NEXTCLOUD_URL", "http://127.0.0.1:8080")
# agent.py stores its signing key in the persistent storage on import
os.environ.setdefault("APP_PERSISTENT_STORAGE", tempfile.mkdtemp())
os.makedirs(os.environ["APP_PERSISTENT_STORAGE"], exist_ok=True)

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from ex_app.lib import agent  # noqa: E402
from ex_app.lib.graph import CONFIRM_TOOLS_NODE, get_graph  # noqa: E402

try:
	import pytest
	parametrize = pytest.mark.parametrize
except ImportError:
	def parametrize(names, values):
		return lambda fn: fn

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "conversation_tokens")
# The key the fixtures were signed with, see mint.py
KEY = 'context_agent conversation token fixtures key'
THREAD = {"configurable": {"thread_id": "thread-1"}}


@tool
def get_weather(city: str) -> str:
	"""Get the current weather for a city."""
	return f'sunny in {city}'


@tool
def send_message(recipient: str, message: str) -> str:
	"""Send a message to someone."""
	return f'sent "{message}" to {recipient}'


WEATHER_CALL = ('get_weather', {'city': 'Berlin'}, 'call_weather')
MESSAGE_CALL = ('send_message', {'recipient': 'Alice', 'message': 'Hi'}, 'call_message')

# What each minted conversation consists of, as (type, content, tool calls or tool_call_id)
ANSWER = [
	('human', 'Hi', None),
	('ai', 'Hello! How can I help?', []),
]
PENDING = [
	('human', 'Tell Alice hi', None),
	('ai', '', [MESSAGE_CALL]),
]
EXPECTED = {
	'answer': (ANSWER, ()),
	'safe_tool': ([
		('human', 'How is the weather in Berlin?', None),
		('ai', '', [WEATHER_CALL]),
		('tool', 'sunny in Berlin', 'call_weather'),
		('ai', 'It is sunny in Berlin.', []),
	], ()),
	'second_turn': (ANSWER + [
		('human', 'What is the capital of France?', None),
		('ai', 'Paris is the capital of France.', []),
	], ()),
	'pending_confirmation': (PENDING, (CONFIRM_TOOLS_NODE,)),
	'confirmed': (PENDING + [
		('tool', 'sent "Hi" to Alice', 'call_message'),
		('ai', 'I sent the message to Alice.', []),
	], ()),
	'denied': (PENDING + [
		('tool', "API call denied by user. Reasoning: 'Not now'.", 'call_message'),
		('ai', 'Okay, I did not send it.', []),
	], ()),
}


def load_fixtures():
	fixtures = {}
	for path in sorted(glob.glob(os.path.join(FIXTURES_DIR, 'v*.json'))):
		with open(path) as f:
			fixture = json.load(f)
		fixtures[fixture['tag']] = fixture
	return fixtures


def mint_current():
	"""Mint tokens with the current code, so mint.py is known to work for the next release

	Returns the fixture and the error of mint.py, if it failed.
	"""
	with tempfile.TemporaryDirectory() as tmp:
		out = os.path.join(tmp, 'HEAD.json')
		result = subprocess.run(
			[sys.executable, os.path.join(FIXTURES_DIR, 'mint.py'), os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'HEAD', out],
			capture_output=True, text=True,
		)
		if result.returncode != 0:
			return None, result.stderr
		with open(out) as f:
			return json.load(f), None


FIXTURES = load_fixtures()
CURRENT, MINT_ERROR = mint_current()
if CURRENT is not None:
	FIXTURES['HEAD'] = CURRENT
CASES = [(tag, scenario) for tag, fixture in FIXTURES.items() for scenario in fixture['tokens']]


def load(token):
	"""Load a token the way agent.react does, falling back to the old token type"""
	agent.key = KEY
	try:
		return agent.load_conversation(token)
	except Exception:
		return agent.load_conversation_old(token)


def export(checkpointer):
	agent.key = KEY
	return agent.export_conversation(checkpointer)


def describe(messages):
	described = []
	for message in messages:
		if isinstance(message, AIMessage):
			extra = [(tc['name'], tc['args'], tc['id']) for tc in message.tool_calls]
		elif isinstance(message, ToolMessage):
			extra = message.tool_call_id
		else:
			assert isinstance(message, HumanMessage), f'unexpected message {message!r}'
			extra = None
		described.append((message.type, message.content, extra))
	return described


async def graph_for(checkpointer, script=()):
	responses = iter(script)

	async def call_model(state, config):
		return {'messages': [next(responses)]}

	graph, _classifications = await get_graph(call_model, [get_weather, send_message], checkpointer)
	return graph


def test_every_release_has_fixtures():
	try:
		tags = subprocess.run(
			['git', 'tag', '--list', 'v*'], cwd=FIXTURES_DIR, capture_output=True, text=True, check=True,
		).stdout.split()
	except (OSError, subprocess.CalledProcessError):
		return
	missing = sorted(set(tags) - set(FIXTURES))
	assert not missing, f'No conversation token fixtures for {missing}, run tests/fixtures/conversation_tokens/mint_all.sh'
	for tag, fixture in FIXTURES.items():
		assert set(fixture['tokens']) == set(EXPECTED), f'{tag} has fixtures for {sorted(fixture["tokens"])}'


def test_current_code_mints_tokens():
	assert MINT_ERROR is None, f'mint.py does not work with the current code, it has to for the next release:\n{MINT_ERROR}'


@parametrize('tag,scenario', CASES)
def test_token_is_read(tag, scenario):
	token = FIXTURES[tag]['tokens'][scenario]
	expected_messages, expected_next = EXPECTED[scenario]

	async def run():
		graph = await graph_for(load(token))
		snapshot = graph.get_state(THREAD)
		assert describe(snapshot.values['messages']) == expected_messages
		assert snapshot.next == expected_next

	asyncio.run(run())


@parametrize('tag,scenario', CASES)
def test_conversation_continues(tag, scenario):
	token = FIXTURES[tag]['tokens'][scenario]
	expected_messages, expected_next = EXPECTED[scenario]

	async def run():
		checkpointer = load(token)
		if expected_next == (CONFIRM_TOOLS_NODE,):
			# the user confirms the pending tool call
			new_input = None
			added = [('tool', 'sent "Hi" to Alice', 'call_message'), ('ai', 'Done.', [])]
		else:
			new_input = {'messages': [('user', 'Thanks!')]}
			added = [('human', 'Thanks!', None), ('ai', 'Done.', [])]
		graph = await graph_for(checkpointer, [AIMessage(content='Done.')])
		async for _ in graph.astream(new_input, THREAD, stream_mode='values'):
			pass

		new_token = export(checkpointer)
		graph = await graph_for(load(new_token))
		snapshot = graph.get_state(THREAD)
		assert describe(snapshot.values['messages']) == expected_messages + added
		assert snapshot.next == ()

	asyncio.run(run())


if __name__ == "__main__":
	failures = 0
	for name, fn in list(globals().items()):
		if not name.startswith("test_") or not callable(fn):
			continue
		cases = CASES if fn.__code__.co_argcount == 2 else [()]
		for case in cases:
			try:
				fn(*case)
				print(f"ok    {name}{list(case) if case else ''}")
			except Exception as e:
				failures += 1
				print(f"FAIL  {name}{list(case) if case else ''}: {e!r}")
	raise SystemExit(1 if failures else 0)
