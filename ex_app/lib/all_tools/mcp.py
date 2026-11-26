# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import timedelta
from json import JSONDecodeError
from urllib.parse import urlsplit

import httpcore
import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient
from nc_py_api import AsyncNextcloudApp
import json
import re
from packaging.version import Version
from ex_app.lib.all_tools.lib.impulse import MCP_ALWAYS_ALLOW_SETTING_ID, always_allow, from_mcp_annotations
from ex_app.lib.all_tools.lib.url_guard import BlockedUrlError, PublicNetworkBackend, assert_public_url
from ex_app.lib.logger import log
from nc_py_api.ex_app import LogLvl
import asyncio
import traceback

# The assistant app stores the MCP servers users add in their personal settings
USER_SERVERS_URL = '/ocs/v2.php/apps/assistant/api/v1/mcp-servers/connections'
MINIMUM_ASSISTANT_VERSION = '4.2.0'

# Limits for the servers of a user. The assistant app enforces the same ones, but it
# only stores what the user entered, so they are checked again here.
MAX_USER_SERVERS = 5
MAX_TOOLS_PER_USER_SERVER = 50
MAX_USER_TOOL_DESCRIPTION_LENGTH = 2000
# The input schema goes to the model too, so it is measured as JSON. Tools above this are left out.
MAX_USER_TOOL_SCHEMA_LENGTH = 10000
USER_SERVER_TIMEOUT = 20
# Most model APIs refuse other tool names
TOOL_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
# Tools of users' servers are named "mcp_user_<server key>__<tool>" and carry the server key in this metadata key.
# Server keys never contain "__" nor end with "_", so the first "__" always ends the key and no two
# (server, tool) pairs share a name.
USER_TOOL_PREFIX = 'mcp_user_'
USER_TOOL_SEPARATOR = '__'
USER_SERVER_METADATA_KEY = 'user_mcp_server'
# The prefix, the key and the separator come out of the 64 characters of a tool name, so
# a short key leaves room for the tool's own name: 41 characters with this limit.
MAX_USER_SERVER_KEY_LENGTH = 12
HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,256}$")
# Headers that would change how the request is framed or routed, not just authorize it
FORBIDDEN_HEADERS = {'host', 'content-length', 'transfer-encoding', 'connection', 'upgrade', 'te', 'trailer'}


class UserServerRejected(Exception):
	"""A server of a user that is not connected to; the message says why."""


async def get_tools(nc: AsyncNextcloudApp):
	admin_tools, user_tools = await asyncio.gather(get_admin_tools(nc), get_user_tools(nc))
	admin_names = {tool.name for tool in admin_tools}
	tools = list(admin_tools)
	for tool in user_tools:
		if tool.name in admin_names:
			await log(nc, LogLvl.WARNING, f"Skipping the tool '{tool.name}' of user {await nc.user}: an MCP server of the admin has a tool of that name")
			continue
		tools.append(tool)
	return tools


async def get_admin_tools(nc: AsyncNextcloudApp):
	mcp_json = await nc.appconfig_ex.get_value("mcp_config", "{}")
	try:
		mcp_config = json.loads(mcp_json)
	except JSONDecodeError:
		await log(nc, LogLvl.ERROR, "Invalid MCP json config: " + mcp_json)
		mcp_config = {}
	always_allowed = parse_always_allow(await nc.appconfig_ex.get_value(MCP_ALWAYS_ALLOW_SETTING_ID, default=""))
	try:
		mcp_config, trusted_servers = split_trusted_servers(mcp_config)
		server = MultiServerMCPClient(mcp_config)
		names = list(mcp_config)
		tools_per_server = await asyncio.wait_for(
			asyncio.gather(*(server.get_tools(server_name=name) for name in names)),
			timeout=120,
		)
		tools = []
		for name, server_tools in zip(names, tools_per_server):
			for tool in server_tools:
				# MCP tools cannot be classified, so they are confirmed as the worst case
				# unless the admin vouched for them or for the annotations of their server
				if "*" in always_allowed or tool.name in always_allowed:
					always_allow(tool.coroutine)
				elif name in trusted_servers:
					from_mcp_annotations(tool.coroutine, tool.metadata)
				tool.name = "mcp_" + tool.name
				tools.append(tool)
		return tools
	except Exception as e:
		tb_str = "".join(traceback.format_exception(e))
		await log(nc, LogLvl.ERROR, "Failed to load MCP servers: " + tb_str)
	return []


async def get_user_tools(nc: AsyncNextcloudApp):
	"""Load the tools of the MCP servers the current user added in the assistant app.

	Nobody vouched for these servers, so none of the admin's trust settings apply to
	them: their tools are always confirmed as the worst case, whatever they are
	called and whatever annotations they declare. A server that cannot be reached or
	is refused is left out, without affecting the others.
	"""
	async def build(server):
		try:
			return await build_user_connection(server)
		except UserServerRejected as e:
			await log(nc, LogLvl.WARNING, f"Skipping an MCP server of user {await nc.user}: {e}")
			return None
		except Exception as e:
			# Whatever the user entered, it must not take the other servers and tools down with it
			await log(nc, LogLvl.WARNING, f"Skipping an MCP server of user {await nc.user}: {e!r}")
			return None

	connections = {}
	for built in await asyncio.gather(*(build(server) for server in await fetch_user_servers(nc))):
		if built is None:
			continue
		key, connection = built
		if key in connections:
			await log(nc, LogLvl.WARNING, f"Skipping an MCP server of user {await nc.user}: the name '{key}' is used twice")
			continue
		connections[key] = connection
	if not connections:
		return []

	client = MultiServerMCPClient(connections)
	keys = list(connections)
	results = await asyncio.gather(
		*(asyncio.wait_for(client.get_tools(server_name=key), timeout=USER_SERVER_TIMEOUT) for key in keys),
		return_exceptions=True,
	)
	tools = []
	taken_names = set()
	for key, result in zip(keys, results):
		if isinstance(result, BaseException):
			await log(nc, LogLvl.WARNING, f"Failed to load the MCP server '{key}' of user {await nc.user}: {result!r}")
			continue
		for tool in result[:MAX_TOOLS_PER_USER_SERVER]:
			name = f"{USER_TOOL_PREFIX}{key}{USER_TOOL_SEPARATOR}{tool.name}"
			if not TOOL_NAME_PATTERN.match(name) or name in taken_names:
				await log(nc, LogLvl.WARNING, f"Skipping the tool '{tool.name}' of the MCP server '{key}' of user {await nc.user}: its name is invalid or taken")
				continue
			if schema_length(tool) > MAX_USER_TOOL_SCHEMA_LENGTH:
				await log(nc, LogLvl.WARNING, f"Skipping the tool '{tool.name}' of the MCP server '{key}' of user {await nc.user}: its input schema is too large")
				continue
			tool.name = name
			tool.description = (tool.description or "")[:MAX_USER_TOOL_DESCRIPTION_LENGTH]
			tool.metadata = {**(tool.metadata or {}), USER_SERVER_METADATA_KEY: key}
			taken_names.add(name)
			tools.append(tool)
	return tools


def schema_length(tool) -> int:
	"""Measure the input schema of a tool as the JSON the model gets."""
	schema = tool.args_schema
	if hasattr(schema, 'model_json_schema'):
		schema = schema.model_json_schema()
	return len(json.dumps(schema, default=str))


async def fetch_user_servers(nc: AsyncNextcloudApp) -> list:
	"""Fetch the enabled MCP servers of the current user from the assistant app."""
	try:
		caps = await nc.capabilities
		if Version(caps['assistant']['version']) < Version(MINIMUM_ASSISTANT_VERSION):
			return []
	except Exception:
		return []
	try:
		res = await nc.ocs('GET', USER_SERVERS_URL)
	except Exception as e:
		await log(nc, LogLvl.WARNING, f"Failed to fetch the MCP servers of user {await nc.user}: {e!r}")
		return []
	if not isinstance(res, dict) or res.get('enabled') is not True:
		return []
	servers = res.get('servers')
	return servers[:MAX_USER_SERVERS] if isinstance(servers, list) else []


async def build_user_connection(server) -> tuple[str, dict]:
	"""Turn a server of a user into a connection the MCP client accepts.

	The connection is built from scratch, key by key, so nothing the user entered
	can pick a different transport (stdio would run a command in this container) or
	set any client option besides the URL and the headers.
	"""
	if not isinstance(server, dict):
		raise UserServerRejected("malformed entry")

	key = server_key(server.get('name'))

	url = server.get('url')
	try:
		# urlsplit raises a ValueError for a URL it cannot parse, and so does reading the port
		is_https = isinstance(url, str) and urlsplit(url).scheme == 'https' and urlsplit(url).port != 0
	except ValueError:
		raise UserServerRejected(f"'{key}' has a malformed URL")
	if not is_https:
		raise UserServerRejected(f"'{key}' needs an https URL")
	try:
		await assert_public_url(url)
	except BlockedUrlError as e:
		raise UserServerRejected(f"'{key}': {e}")
	except ValueError:
		raise UserServerRejected(f"'{key}' has a malformed URL")

	headers = server.get('headers') or {}
	if not isinstance(headers, dict):
		raise UserServerRejected(f"'{key}' has malformed headers")
	for header, value in headers.items():
		if (
			not isinstance(header, str)
			or not HEADER_NAME_PATTERN.match(header)
			or header.lower() in FORBIDDEN_HEADERS
			or not isinstance(value, str)
			or '\r' in value or '\n' in value
		):
			raise UserServerRejected(f"'{key}' has a header that is not allowed")

	return key, {
		"transport": "streamable_http",
		"url": url,
		"headers": dict(headers),
		"timeout": timedelta(seconds=USER_SERVER_TIMEOUT),
		"httpx_client_factory": public_http_client,
	}


def server_key(name) -> str:
	"""Derive the name a user's server is known by from the name the user gave it.

	It goes into the names of the server's tools, so it is kept short and limited to
	the characters tool names allow. Keep in sync with McpServersService::serverKey()
	in the assistant app, which refuses names that would give two servers one key.
	"""
	key = re.sub(r'[^A-Za-z0-9]+', '_', str(name or '')).lower().strip('_')[:MAX_USER_SERVER_KEY_LENGTH].strip('_')
	if not key:
		raise UserServerRejected("the server has no usable name")
	return key


def public_http_client(
	headers: dict[str, str] | None = None,
	timeout: httpx.Timeout | None = None,
	auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
	"""Create an HTTP client for an MCP server of a user that only reaches public addresses.

	The MCP client calls this for listing the tools and for every tool call, so the
	address check applies to all of them, not only to the URL checked up front.
	Proxies from the environment are ignored: a proxy would resolve the host itself
	and get around the check.
	"""
	transport = httpx.AsyncHTTPTransport(trust_env=False)
	# httpx has no way to pass a network backend, so its pool is replaced by one that
	# uses ours. The new pool has the connection limits of httpcore, not those of httpx,
	# which is plenty for one MCP server. The pool is private to httpx (pinned in
	# pyproject.toml): if it moves, no client is made rather than one without the check.
	if not isinstance(getattr(transport, '_pool', None), httpcore.AsyncConnectionPool):
		raise RuntimeError("cannot install the address check in this httpx version")
	transport._pool = httpcore.AsyncConnectionPool(
		ssl_context=httpx.create_ssl_context(trust_env=False),
		network_backend=PublicNetworkBackend(),
	)
	return httpx.AsyncClient(
		transport=transport,
		headers=headers,
		timeout=timeout or httpx.Timeout(30, read=300),
		auth=auth,
		trust_env=False,
		follow_redirects=False,
	)


def split_trusted_servers(mcp_config: dict) -> tuple[dict, set[str]]:
	"""Take the "trust_annotations" flag out of each server's config.

	The flag is ours, not the MCP client's, so it is removed before the config
	reaches the client. Returns the cleaned config and the names of the servers
	whose tool annotations the admin trusts.
	"""
	cleaned = {}
	trusted = set()
	for name, connection in mcp_config.items():
		if isinstance(connection, dict):
			connection = dict(connection)
			if connection.pop("trust_annotations", False) is True:
				trusted.add(name)
		cleaned[name] = connection
	return cleaned, trusted


def parse_always_allow(value) -> set[str]:
	"""Turn the admin's list of always allowed MCP tools into a set of names.

	Names are taken with or without the "mcp_" prefix the agent shows them with.
	"""
	names = set()
	for name in re.split(r"[,\s]+", str(value or "")):
		if name.startswith("mcp_"):
			name = name[len("mcp_"):]
		if name:
			names.add(name)
	return names


def get_category_name():
	return "MCP Server"


async def is_available(nc: AsyncNextcloudApp):
	return True
