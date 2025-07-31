# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from json import JSONDecodeError

from langchain_mcp_adapters.client import MultiServerMCPClient
from nc_py_api import Nextcloud
import json
from ex_app.lib.logger import log
from nc_py_api.ex_app import LogLvl
import asyncio
import traceback


async def get_tools(nc: Nextcloud):
	mcp_json = nc.appconfig_ex.get_value("mcp_config", "{}")
	try:
		mcp_config = json.loads(mcp_json)
	except JSONDecodeError:
		log(nc, LogLvl.ERROR, "Invalid MCP json config: " + mcp_json)
		mcp_config = {}
	try:
		server = MultiServerMCPClient(mcp_config)
		tools = await asyncio.wait_for(server.get_tools(), timeout=120)
		for tool in tools:
			tool.name = "mcp_" + tool.name
		return tools
	except Exception as e:
		tb_str = "".join(traceback.format_exception(e))
		log(nc, LogLvl.ERROR, "Failed to load MCP servers: " + tb_str)
	return []


def get_category_name():
	return "MCP Server"


def is_available(nc: Nextcloud):
	return True
