# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import re

from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool

async def get_tools(nc: AsyncNextcloudApp):
    tools = []
    providers = await nc.ocs('GET', '/ocs/v2.php/search/providers')

    for provider in providers:
        def make_tool(p):
            async def tool(search_query: dict[str, str]):
                results = await nc.ocs('GET', f"/ocs/v2.php/search/providers/{p['id']}/search", params=search_query)
                return json.dumps(results['entries'])
            return tool

        tool_func = make_tool(provider)
        tool_func.__name__ = re.sub(r"[^a-zA-Z0-9_-]+", '_', "search_" + provider['name'].lower())
        tool_func.__doc__ = (
            f"Searches {provider['name']} in Nextcloud.\n"
            f":param search_query: A mapping of filter names to filter values to use for the search. "
            f"Choose filters from {json.dumps(provider['filters'])}. (The 'person' filter, if available, takes a userID. Use find_person_in_users to obtain it.)"
            'For example: {"term": "hans", ...}\n'
        )
        tools.append(tool(safe_tool(tool_func)))

    return tools

def get_category_name():
    return "Unified Search"

async def is_available(nc: AsyncNextcloudApp):
    return True
