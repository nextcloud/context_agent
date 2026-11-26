# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The MCP servers users add in the assistant app: what is connected to, and how.

Runnable standalone (``python tests/test_user_mcp_servers.py``) or via pytest.
No network: URLs use public IP literals, which are checked without DNS, and the
only connections made go to a local server that must not be reached.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpcore  # noqa: E402
import httpx  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from ex_app.lib.all_tools import mcp  # noqa: E402
from ex_app.lib.all_tools.lib.impulse import (  # noqa: E402
	ImpulseRadius,
	Reversibility,
	classify_reversibility,
	classify_tool_call,
	is_always_allowed,
)
from ex_app.lib.all_tools.lib import url_guard  # noqa: E402
from ex_app.lib.all_tools.lib.url_guard import PublicNetworkBackend  # noqa: E402

PUBLIC_URL = 'https://1.1.1.1/mcp'


def _rejected(server) -> bool:
	try:
		asyncio.run(mcp.build_user_connection(server))
	except mcp.UserServerRejected:
		return True
	return False


def test_connection_is_rebuilt_from_url_and_headers_only():
	key, connection = asyncio.run(mcp.build_user_connection({
		'name': 'My Weather!',
		'url': PUBLIC_URL,
		'headers': {'Authorization': 'Bearer abc'},
		# none of these may reach the MCP client
		'transport': 'stdio',
		'command': 'sh',
		'args': ['-c', 'id'],
		'trust_annotations': True,
	}))
	assert key == 'my_weather'
	assert connection == {
		'transport': 'streamable_http',
		'url': PUBLIC_URL,
		'headers': {'Authorization': 'Bearer abc'},
		'timeout': connection['timeout'],
		'httpx_client_factory': mcp.public_http_client,
	}


def test_server_key_leaves_room_for_the_tool_name():
	key = mcp.server_key('A very long name for a server')
	assert key == 'a_very_long'
	assert len(key) <= mcp.MAX_USER_SERVER_KEY_LENGTH
	name = f"{mcp.USER_TOOL_PREFIX}{'k' * mcp.MAX_USER_SERVER_KEY_LENGTH}{mcp.USER_TOOL_SEPARATOR}{'t' * 41}"
	assert mcp.TOOL_NAME_PATTERN.match(name)


def test_backend_tries_the_next_address_after_a_timeout():
	attempts = []

	async def resolve(host):
		return ['1.1.1.1', '1.0.0.1']

	async def connect(self, host, port, *args, **kwargs):
		attempts.append(host)
		if host == '1.1.1.1':
			raise httpcore.ConnectTimeout('no answer')
		return 'stream'

	originals = (url_guard._resolve_all, httpcore.AnyIOBackend.connect_tcp)
	url_guard._resolve_all, httpcore.AnyIOBackend.connect_tcp = resolve, connect
	try:
		assert asyncio.run(PublicNetworkBackend().connect_tcp('example.com', 443)) == 'stream'
	finally:
		url_guard._resolve_all, httpcore.AnyIOBackend.connect_tcp = originals
	assert attempts == ['1.1.1.1', '1.0.0.1']


def test_refused_servers():
	assert _rejected('https://1.1.1.1/mcp')
	assert _rejected({'name': 'a', 'url': 'http://1.1.1.1/mcp'})
	assert _rejected({'name': 'a', 'url': 'https://127.0.0.1/mcp'})
	assert _rejected({'name': 'a', 'url': 'https://169.254.169.254/latest'})
	assert _rejected({'name': 'a', 'url': 'https://[::ffff:10.0.0.1]/mcp'})
	assert _rejected({'name': 'a', 'url': 'https://localhost/mcp'})
	assert _rejected({'name': '!!!', 'url': PUBLIC_URL})
	# URLs that urlsplit cannot parse
	assert _rejected({'name': 'a', 'url': 'https://[::1/mcp'})
	assert _rejected({'name': 'a', 'url': 'https://1.1.1.1:99999/mcp'})
	assert _rejected({'name': 'a', 'url': 'https://1.1.1.1:x/mcp'})
	assert _rejected({'name': 'a', 'url': PUBLIC_URL, 'headers': ['Authorization']})
	assert _rejected({'name': 'a', 'url': PUBLIC_URL, 'headers': {'Host': 'internal'}})
	assert _rejected({'name': 'a', 'url': PUBLIC_URL, 'headers': {'X-A': 'b\r\nX-B: c'}})
	assert _rejected({'name': 'a', 'url': PUBLIC_URL, 'headers': {'Bad Name': 'b'}})
	assert _rejected({'name': 'a', 'url': PUBLIC_URL, 'headers': {'X-A': 1}})


async def _local_server():
	async def handle(reader, writer):
		writer.write(b'HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok')
		await writer.drain()
		writer.close()
	server = await asyncio.start_server(handle, '127.0.0.1', 0)
	return server, server.sockets[0].getsockname()[1]


def test_backend_refuses_internal_addresses():
	async def run():
		server, port = await _local_server()
		async with server:
			# reachable without the guard ...
			async with httpx.AsyncClient(trust_env=False) as plain:
				assert (await plain.get(f'http://127.0.0.1:{port}/')).text == 'ok'
			# ... but not through the client user servers get
			async with mcp.public_http_client() as guarded:
				try:
					await guarded.get(f'http://127.0.0.1:{port}/')
				except httpx.ConnectError as e:
					assert 'non-public' in str(e)
				else:
					raise AssertionError('the guarded client reached 127.0.0.1')
			try:
				await PublicNetworkBackend().connect_tcp('localhost', port)
			except httpcore.ConnectError:
				pass
			else:
				raise AssertionError('the backend connected to localhost')
	asyncio.run(run())


class _FakeNextcloud:
	def __init__(self, response, version='4.2.0'):
		self.response = response
		self.version = version

	@property
	async def user(self):
		return 'alice'

	@property
	async def capabilities(self):
		return {'assistant': {'version': self.version}}

	async def ocs(self, method, path):
		assert (method, path) == ('GET', mcp.USER_SERVERS_URL)
		return self.response

	class appconfig_ex:
		@staticmethod
		async def get_value(key, default=None):
			# the admin allows every tool and trusts every annotation of their servers
			return '*' if key == mcp.MCP_ALWAYS_ALLOW_SETTING_ID else default


@tool
async def forecast(city: str):
	"""Get the forecast."""


@tool
async def mail_everything():
	"""Mail everything."""


class _FakeClient:
	connected = []

	def __init__(self, connections):
		self.connections = connections

	async def get_tools(self, server_name):
		_FakeClient.connected.append(self.connections[server_name]['url'])
		if server_name == 'broken':
			raise ConnectionError('down')
		huge = {'type': 'object', 'properties': {'q': {'type': 'string', 'description': 'x' * 20000}}}
		return [
			forecast.model_copy(),
			mail_everything.model_copy(update={'description': 'x' * 5000}),
			mail_everything.model_copy(update={'name': 'huge', 'args_schema': huge}),
		]


def test_user_tools():
	async def silent_log(*args):
		pass

	originals = (mcp.MultiServerMCPClient, mcp.log)
	mcp.MultiServerMCPClient, mcp.log = _FakeClient, silent_log
	try:
		nc = _FakeNextcloud({'enabled': True, 'servers': [
			{'name': 'Weather', 'url': PUBLIC_URL},
			{'name': ' WEATHER!', 'url': 'https://1.0.0.1/mcp'},
			{'name': 'broken', 'url': PUBLIC_URL},
			{'name': 'internal', 'url': 'https://10.0.0.1/mcp'},
			# a URL that cannot be parsed costs only its own server
			{'name': 'malformed', 'url': 'https://[::1/mcp'},
		]})
		tools = asyncio.run(mcp.get_user_tools(nc))
		# the second server with the same key, the broken and the internal server are left out,
		# and so is the tool with the oversized schema
		assert '1.0.0.1' not in ' '.join(_FakeClient.connected)
		assert [t.name for t in tools] == ['mcp_user_weather__forecast', 'mcp_user_weather__mail_everything']
		tools = tools[1:]
		assert tools[0].metadata[mcp.USER_SERVER_METADATA_KEY] == 'weather'
		assert len(tools[0].description) == mcp.MAX_USER_TOOL_DESCRIPTION_LENGTH
		# none of the admin's trust reaches user tools: they are the worst case
		assert not is_always_allowed(tools[0])
		assert asyncio.run(classify_tool_call(tools[0], {})) == ImpulseRadius.EXTERNAL
		assert asyncio.run(classify_reversibility(tools[0], {})) == Reversibility.IRREVERSIBLE

		# a user tool with the name of an admin tool is left out
		async def admin_tools(nc):
			return [forecast.model_copy(update={'name': 'mcp_user_weather__forecast'})]
		original_admin_tools, mcp.get_admin_tools = mcp.get_admin_tools, admin_tools
		try:
			names = [t.name for t in asyncio.run(mcp.get_tools(nc))]
		finally:
			mcp.get_admin_tools = original_admin_tools
		assert names == ['mcp_user_weather__forecast', 'mcp_user_weather__mail_everything']

		# the admin has not allowed user servers, or the assistant is too old
		assert asyncio.run(mcp.get_user_tools(_FakeNextcloud({'enabled': False, 'servers': [
			{'name': 'Weather', 'url': PUBLIC_URL},
		]}))) == []
		assert asyncio.run(mcp.get_user_tools(_FakeNextcloud({'enabled': True, 'servers': [
			{'name': 'Weather', 'url': PUBLIC_URL},
		]}, version='4.1.0'))) == []
	finally:
		mcp.MultiServerMCPClient, mcp.log = originals


if __name__ == '__main__':
	test_connection_is_rebuilt_from_url_and_headers_only()
	test_server_key_leaves_room_for_the_tool_name()
	test_backend_tries_the_next_address_after_a_timeout()
	test_refused_servers()
	test_backend_refuses_internal_addresses()
	test_user_tools()
	print('ok')
