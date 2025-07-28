# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib
import os
import pathlib
import json
from os.path import dirname

from langchain_mcp_adapters.client import MultiServerMCPClient
from nc_py_api import Nextcloud
from ex_app.lib.all_tools.lib.decorator import timed_memoize

@timed_memoize(1*60)
async def get_tools(nc: Nextcloud):
	directory = dirname(__file__) + '/all_tools'
	function_name = "get_tools"

	dangerous_tools = []
	safe_tools = []

	py_files = [f for f in os.listdir(directory) if f.endswith(".py") and f != "__init__.py"]
	is_activated = json.loads(nc.appconfig_ex.get_value('tool_status'))

	for file in py_files:
		# Load module dynamically
		module_name, spec, module = get_tool_module(file, directory)

		# Call function if it exists
		if hasattr(module, function_name):
			get_tools_from_import = getattr(module, function_name)
			available_from_import = getattr(module, "is_available")
			if not is_activated[module_name]:
				print(f"{module_name} tools deactivated")
				continue
			if not available_from_import(nc):
				print(f"{module_name} not available")
				continue
			if callable(get_tools_from_import):
				print(f"Invoking {function_name} from {module_name}")
				imported_tools = await get_tools_from_import(nc)
				for tool in imported_tools:
					if not hasattr(tool, 'func') or not hasattr(tool.func, 'safe'):
						safe_tools.append(tool) # external tools cannot be decorated and should always be safe
						continue
					if not tool.func.safe:
						dangerous_tools.append(tool)
					else:
						safe_tools.append(tool)
			else:
				print(f"{function_name} in {module_name} is not callable.")
		else:
			print(f"{function_name} not found in {module_name}.")

	return safe_tools, dangerous_tools

def get_categories():
	directory = dirname(__file__) + '/all_tools'
	function_name = "get_category_name"

	categories = {}

	py_files = [f for f in os.listdir(directory) if f.endswith(".py") and f != "__init__.py"]

	for file in py_files:
		# Load module dynamically
		module_name, spec, module = get_tool_module(file, directory)

		# Call function if it exists
		if hasattr(module, function_name):
			category_from_import = getattr(module, function_name)
			if callable(category_from_import):
				categories[module_name] = category_from_import()
			else:
				print(f"{function_name} in {module_name} is not callable.")

	return categories


def get_tool_module(file, directory):
	module_name = pathlib.Path(file).stem  # Extract module name without .py
	module_path = os.path.join(directory, file)

	spec = importlib.util.spec_from_file_location(module_name, module_path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)

	return module_name, spec, module