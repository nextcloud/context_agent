import json

from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool

async def get_tools(nc: AsyncNextcloudApp):
    tools = []
    providers = await nc.ocs('GET', '/ocs/v2.php/search/providers')

    for provider in providers:
        def make_tool(p):
            async def tool(search_query: dict[str, str]):
                results = await nc.ocs('GET', f'/ocs/v2.php/search/providers/{p['id']}/search', params=search_query)
                return json.dumps(results['entries'])
            return tool

        tool_func = make_tool(provider)
        tool_func.__name__ = "search_" + provider['name'].lower()
        tool_func.__doc__ = f"Searches {provider['name']} in Nextcloud\n:param search_query: A list of filters to use for searches. Choose filters from {json.dumps(provider['filters'])}" + \
        ' e.g. in the form of {"term": "hans", ...}\n'
        tools.append(tool(safe_tool(tool_func)))

    return tools

def get_category_name():
    return "Unified Search"

async def is_available(nc: AsyncNextcloudApp):
    return True
