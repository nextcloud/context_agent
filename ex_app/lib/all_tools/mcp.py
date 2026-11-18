# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from json import JSONDecodeError

from langchain_mcp_adapters.client import MultiServerMCPClient
from nc_py_api import AsyncNextcloudApp
import json
import re
from ex_app.lib.all_tools.lib.impulse import MCP_ALWAYS_ALLOW_SETTING_ID, always_allow, from_mcp_annotations
from ex_app.lib.logger import log
from nc_py_api.ex_app import LogLvl
import asyncio
import traceback


async def get_tools(nc: AsyncNextcloudApp):
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
