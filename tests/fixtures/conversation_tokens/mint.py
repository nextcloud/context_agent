# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mint conversation_token fixtures with the code of an old release.

This is not a test. It is run by ``mint_all.sh`` once per release tag, with the
sources of that tag and an environment holding the dependencies that tag shipped
with, to record what the tokens of that release looked like. The tests also run
it on the current code, as tag HEAD:

	python mint.py <checkout of the tag> <tag> <output.json>

It drives the release's own get_graph, load_conversation/export_conversation,
signature and (where vendored) checkpointer and serializer modules, so the
tokens are exactly what that release handed out. Only the model is scripted.
"""
import ast
import asyncio
import inspect
import json
import os
import sys
from importlib import metadata

# Fixed key so the test can verify the signatures of the minted tokens
KEY = 'context_agent conversation token fixtures key'

src, tag, out_path = sys.argv[1:4]
lib = os.path.join(src, 'ex_app', 'lib')
# early releases import their modules top-level (`from signature import ...`)
sys.path.insert(0, lib)
sys.path.insert(0, src)

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402


@tool
def get_weather(city: str) -> str:
	"""Get the current weather for a city."""
	return f'sunny in {city}'


@tool
def send_message(recipient: str, message: str) -> str:
	"""Send a message to someone."""
	return f'sent "{message}" to {recipient}'


def load_agent_namespace():
	"""Execute the token-related parts of the release's agent.py without the rest of it

	agent.py pulls in the model, Nextcloud client and tools on import, so only the
	imports of the token modules, `thread` and the load/export functions are run.
	"""
	with open(os.path.join(lib, 'agent.py')) as f:
		tree = ast.parse(f.read())
	modules = {
		'signature', 'ex_app.lib.signature',
		'ex_app.lib.memorysaver', 'ex_app.lib.jsonplus',
		'langgraph.checkpoint.memory',
	}
	keep = []
	for node in tree.body:
		if isinstance(node, ast.ImportFrom) and node.module in modules:
			keep.append(node)
		elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('thread', 'graph_config') for t in node.targets):
			keep.append(node)
		elif isinstance(node, ast.FunctionDef) and node.name.startswith(('load_conversation', 'export_conversation')):
			keep.append(node)
	namespace = {'key': KEY}
	exec(compile(ast.Module(body=keep, type_ignores=[]), 'agent.py', 'exec'), namespace)
	return namespace


def import_graph_module():
	try:
		import ex_app.lib.graph as graph_module
	except ImportError:
		import graph as graph_module
	return graph_module


ns = load_agent_namespace()
get_graph = import_graph_module().get_graph
# renamed to graph_config after 2.9.1
thread = ns['thread'] if 'thread' in ns else ns['graph_config']
# releases up to 2.9.1 take the safe and the dangerous tools separately, later ones
# take all tools and decide per call by its impulse radius
split_tools = 'safe_tools' in inspect.signature(get_graph).parameters
if not split_tools:
	from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse
	# get_weather reaches nobody, so it runs without confirmation, while send_message
	# carries no hook and is confirmed like any tool of unknown reach
	impulse(ImpulseRadius.SELF)(get_weather.func)


def build_graph(call_model, checkpointer):
	if split_tools:
		return get_graph(call_model, [get_weather], [send_message], checkpointer)
	return get_graph(call_model, [get_weather, send_message], checkpointer)


def scripted_model(script):
	"""A call_model node that answers with the next message of the script"""
	responses = iter(script)

	def call_model(state, config):
		return {'messages': [next(responses)]}

	async def acall_model(state, config):
		return call_model(state, config)

	return acall_model if inspect.iscoroutinefunction(get_graph) else call_model


def load(token):
	if 'checkpointer' in inspect.signature(ns['load_conversation']).parameters:
		# releases before 2.2.0 load into a checkpointer they create themselves
		checkpointer = ns['MemorySaver']()
		ns['load_conversation'](checkpointer, token)
		return checkpointer
	try:
		return ns['load_conversation'](token)
	except Exception:
		return ns['load_conversation_old'](token)


async def run_turn(token, script, confirmation=None, user_input=''):
	"""One task of the release: load the token, run the graph, export the new token"""
	checkpointer = load(token)
	graph = build_graph(scripted_model(script), checkpointer)
	if inspect.isawaitable(graph):
		graph = await graph
	if isinstance(graph, tuple):
		# returned along with the call classifications after 2.9.1
		graph, _classifications = graph

	snapshot = graph.get_state(thread)
	if snapshot.next == ('dangerous_tools',):
		if confirmation == 0:
			new_input = {'messages': [
				ToolMessage(tool_call_id=tool_call['id'], content=f"API call denied by user. Reasoning: '{user_input}'.")
				for tool_call in snapshot.values['messages'][-1].tool_calls
			]}
		else:
			new_input = None
	else:
		new_input = {'messages': [('user', user_input)]}

	if inspect.iscoroutinefunction(get_graph):
		async for _ in graph.astream(new_input, thread, stream_mode='values'):
			pass
	else:
		for _ in graph.stream(new_input, thread, stream_mode='values'):
			pass
	return ns['export_conversation'](checkpointer)


def weather_call(call_id):
	return AIMessage(content='', tool_calls=[{'name': 'get_weather', 'args': {'city': 'Berlin'}, 'id': call_id}])


def message_call(call_id):
	return AIMessage(content='', tool_calls=[{'name': 'send_message', 'args': {'recipient': 'Alice', 'message': 'Hi'}, 'id': call_id}])


async def mint():
	tokens = {}
	tokens['answer'] = await run_turn('', [AIMessage(content='Hello! How can I help?')], user_input='Hi')
	tokens['safe_tool'] = await run_turn(
		'', [weather_call('call_weather'), AIMessage(content='It is sunny in Berlin.')], user_input='How is the weather in Berlin?',
	)
	tokens['second_turn'] = await run_turn(
		tokens['answer'], [AIMessage(content='Paris is the capital of France.')], user_input='What is the capital of France?',
	)
	tokens['pending_confirmation'] = await run_turn(
		'', [message_call('call_message')], user_input='Tell Alice hi',
	)
	tokens['confirmed'] = await run_turn(
		tokens['pending_confirmation'], [AIMessage(content='I sent the message to Alice.')], confirmation=1,
	)
	tokens['denied'] = await run_turn(
		tokens['pending_confirmation'], [AIMessage(content='Okay, I did not send it.')], confirmation=0, user_input='Not now',
	)
	return tokens


versions = {}
for package in ('langgraph', 'langgraph-checkpoint', 'langchain-core', 'ormsgpack', 'msgpack', 'pydantic'):
	try:
		versions[package] = metadata.version(package)
	except metadata.PackageNotFoundError:
		pass

with open(out_path, 'w') as f:
	json.dump({
		'tag': tag,
		'python': sys.version.split()[0],
		'versions': versions,
		'tokens': asyncio.run(mint()),
	}, f, indent='\t')
	f.write('\n')
