# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib
import os
import pathlib
import json
from os.path import dirname

from nc_py_api import Nextcloud

def get_tools(nc: Nextcloud):
	directory = dirname(__file__) + '/all_tools'
	function_name = "get_tools"

	dangerous_tools = []
	safe_tools = []

	py_files = [f for f in os.listdir(directory) if f.endswith(".py") and f != "__init__.py"]
	is_activated = json.loads(nc.appconfig_ex.get_value('tool_status'))
	print(is_activated)

	for file in py_files:
		module_name = pathlib.Path(file).stem  # Extract module name without .py
		module_path = os.path.join(directory, file)

		# Load module dynamically
		spec = importlib.util.spec_from_file_location(module_name, module_path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)

		# Call function if it exists
		if hasattr(module, function_name):
			get_tools_from_import = getattr(module, function_name)
			if not is_activated[module_name]:
				print(f"{module_name} tools deactivated")
				continue
			if callable(get_tools_from_import):
				print(f"Invoking {function_name} from {module_name}")
				imported_tools = get_tools_from_import(nc)
				for tool in imported_tools:
					if not tool.func.safe:
						dangerous_tools.append(tool)
					else:
						safe_tools.append(tool)
			else:
				print(f"{function_name} in {module_name} is not callable.")
		else:
			print(f"{function_name} not found in {module_name}.")
	get_categories()

	return safe_tools, dangerous_tools

def get_categories():
	directory = dirname(__file__) + '/all_tools'
	function_name = "get_category_name"

	categories = {}

	py_files = [f for f in os.listdir(directory) if f.endswith(".py") and f != "__init__.py"]

	for file in py_files:
		module_name = pathlib.Path(file).stem  # Extract module name without .py
		module_path = os.path.join(directory, file)

		# Load module dynamically
		spec = importlib.util.spec_from_file_location(module_name, module_path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)

		# Call function if it exists
		if hasattr(module, function_name):
			category_from_import = getattr(module, function_name)
			if callable(category_from_import):
				categories[module_name] = category_from_import()
			else:
				print(f"{function_name} in {module_name} is not callable.")

	return categories
