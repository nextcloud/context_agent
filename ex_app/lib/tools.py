# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib
import json
import os
import pathlib
from os.path import dirname

from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import timed_memoize
from ex_app.lib.all_tools.lib.impulse import (
	DEFAULT_DESTRUCTIVE_THRESHOLD,
	DEFAULT_IMPULSE_THRESHOLD,
	DESTRUCTIVE_THRESHOLD_SETTING_ID,
	IMPULSE_THRESHOLD_SETTING_ID,
	ImpulseRadius,
	parse_impulse_radius,
)


async def _get_threshold(nc: AsyncNextcloudApp, setting_id: str, default: ImpulseRadius) -> ImpulseRadius:
	configured = await nc.appconfig_ex.get_value(setting_id, default=default.name.lower())
	return parse_impulse_radius(configured, default=default)


async def get_impulse_threshold(nc: AsyncNextcloudApp) -> ImpulseRadius:
	"""The configured impulse radius from which on a tool call has to be confirmed."""
	return await _get_threshold(nc, IMPULSE_THRESHOLD_SETTING_ID, DEFAULT_IMPULSE_THRESHOLD)


async def get_destructive_threshold(nc: AsyncNextcloudApp) -> ImpulseRadius:
	"""The configured impulse radius from which on a deletion has to be confirmed."""
	return await _get_threshold(nc, DESTRUCTIVE_THRESHOLD_SETTING_ID, DEFAULT_DESTRUCTIVE_THRESHOLD)


@timed_memoize(1*60)
async def get_tools(nc: AsyncNextcloudApp):
	directory = dirname(__file__) + '/all_tools'
	function_name = "get_tools"

	tools = []

	py_files = [f for f in os.listdir(directory) if f.endswith(".py") and f != "__init__.py"]
	is_activated = json.loads(await nc.appconfig_ex.get_value('tool_status'))

	for file in py_files:
		# Load module dynamically
		module_name, spec, module = get_tool_module(file, directory)

		# Call function if it exists
		if hasattr(module, function_name):
			get_tools_from_import = getattr(module, function_name)
			available_from_import = getattr(module, "is_available")
			if not is_activated.get(module_name, False):
				print(f"{module_name} tools deactivated")
				continue
			if not await available_from_import(nc):
				print(f"{module_name} not available")
				continue
			if callable(get_tools_from_import):
				print(f"Invoking {function_name} from {module_name}")
				imported_tools = await get_tools_from_import(nc)
				# Tools carry their impulse radius hook on the wrapped function; tools
				# without one (MCP tools cannot be decorated) fall back to the widest
				# radius at classification time, so they always need confirmation.
				tools.extend(imported_tools)
			else:
				print(f"{function_name} in {module_name} is not callable.")
		else:
			print(f"{function_name} not found in {module_name}.")

	return tools

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

	# Resolve via the canonical dotted package name so the module is shared
	# with the rest of the codebase via sys.modules. This means module-level
	# state (caches, singletons) survives across `get_tools` calls and a
	# `from ex_app.lib.all_tools.<name> import ...` elsewhere refers to the
	# SAME module object as the one we load here. Trade-off: hot-reloading
	# tool files during development requires a process restart, which is fine.
	qualified = f'ex_app.lib.all_tools.{module_name}'
	module = importlib.import_module(qualified)
	spec = module.__spec__

	return module_name, spec, module