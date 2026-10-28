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
	DEFAULT_IRREVERSIBLE_FROM,
	DEFAULT_IRREVERSIBLE_THRESHOLD,
	DEFAULT_ITEM_COUNT_THRESHOLD,
	DEFAULT_RELAX_REVERSIBLE_DELETIONS,
	DESTRUCTIVE_THRESHOLD_SETTING_ID,
	IMPULSE_THRESHOLD_SETTING_ID,
	IRREVERSIBLE_FROM_SETTING_ID,
	IRREVERSIBLE_THRESHOLD_SETTING_ID,
	ITEM_COUNT_THRESHOLD_SETTING_ID,
	RELAX_REVERSIBLE_DELETIONS_SETTING_ID,
	ConfirmationPolicy,
	ImpulseRadius,
	parse_impulse_radius,
	parse_reversibility,
)


async def _get_threshold(nc: AsyncNextcloudApp, setting_id: str, default: ImpulseRadius) -> ImpulseRadius:
	configured = await nc.appconfig_ex.get_value(setting_id, default=default.name.lower())
	return parse_impulse_radius(configured, default=default)


async def _get_flag(nc: AsyncNextcloudApp, setting_id: str, default: bool) -> bool:
	configured = await nc.appconfig_ex.get_value(setting_id, default='1' if default else '0')
	if isinstance(configured, bool):
		return configured
	return str(configured).strip().lower() in ('1', 'true', 'yes', 'on')


async def _get_count(nc: AsyncNextcloudApp, setting_id: str, default: int) -> int:
	configured = await nc.appconfig_ex.get_value(setting_id, default=str(default))
	try:
		return max(int(str(configured).strip()), 0)
	except ValueError:
		return default


# Cached like get_tools below, and for the same reason: every turn reads these, an
# admin changes them once in a while.
@timed_memoize(1*60)
async def get_confirmation_policy(nc: AsyncNextcloudApp) -> ConfirmationPolicy:
	"""The configured thresholds from which on tool calls have to be confirmed."""
	irreversible_from = await nc.appconfig_ex.get_value(
		IRREVERSIBLE_FROM_SETTING_ID, default=DEFAULT_IRREVERSIBLE_FROM.name.lower(),
	)
	return ConfirmationPolicy(
		impulse_threshold=await _get_threshold(nc, IMPULSE_THRESHOLD_SETTING_ID, DEFAULT_IMPULSE_THRESHOLD),
		destructive_threshold=await _get_threshold(nc, DESTRUCTIVE_THRESHOLD_SETTING_ID, DEFAULT_DESTRUCTIVE_THRESHOLD),
		irreversible_threshold=await _get_threshold(nc, IRREVERSIBLE_THRESHOLD_SETTING_ID, DEFAULT_IRREVERSIBLE_THRESHOLD),
		irreversible_from=parse_reversibility(irreversible_from, default=DEFAULT_IRREVERSIBLE_FROM),
		relax_reversible_deletions=await _get_flag(
			nc, RELAX_REVERSIBLE_DELETIONS_SETTING_ID, DEFAULT_RELAX_REVERSIBLE_DELETIONS,
		),
		item_count_threshold=await _get_count(nc, ITEM_COUNT_THRESHOLD_SETTING_ID, DEFAULT_ITEM_COUNT_THRESHOLD),
	)


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
				# Tools carry their impulse hooks on the wrapped function; tools without
				# them (MCP tools cannot be decorated) fall back to the worst case at
				# classification time, so they always need confirmation.
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