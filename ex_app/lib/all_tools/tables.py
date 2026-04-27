# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):

	# --- Tables ---

	@tool
	@safe_tool
	async def list_tables():
		"""
		List all tables available to the current user in the Nextcloud Tables app
		:return: list of tables with their id, title, emoji, ownership, and column/row counts
		"""
		response = await nc._session._create_adapter().request(
			'GET', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def create_table(title: str, emoji: Optional[str] = None, template: Optional[str] = None):
		"""
		Create a new table in the Nextcloud Tables app
		:param title: the title for the new table
		:param emoji: optional emoji icon for the table (single emoji character)
		:param template: optional template to use (e.g. "todo", "members", "weight")
		:return: the created table with its id
		"""
		payload = {'title': title}
		if emoji is not None:
			payload['emoji'] = emoji
		if template is not None:
			payload['template'] = template
		response = await nc._session._create_adapter().request(
			'POST', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			json=payload,
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def update_table(table_id: int, title: Optional[str] = None, emoji: Optional[str] = None, archived: Optional[bool] = None):
		"""
		Update a table's properties
		:param table_id: the id of the table to update (obtainable with list_tables)
		:param title: new title for the table
		:param emoji: new emoji icon for the table
		:param archived: set to true to archive the table, false to unarchive
		:return: the updated table
		"""
		payload = {}
		if title is not None:
			payload['title'] = title
		if emoji is not None:
			payload['emoji'] = emoji
		if archived is not None:
			payload['archived'] = archived
		response = await nc._session._create_adapter().request(
			'PUT', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables/{table_id}",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			json=payload,
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def delete_table(table_id: int):
		"""
		Delete a table and all its columns and rows
		:param table_id: the id of the table to delete (obtainable with list_tables)
		:return: the deleted table
		"""
		response = await nc._session._create_adapter().request(
			'DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables/{table_id}",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
		)
		return json.dumps(response.json())

	# --- Columns ---

	@tool
	@safe_tool
	async def list_columns(table_id: int):
		"""
		List all columns defined for a table
		:param table_id: the id of the table (obtainable with list_tables)
		:return: list of columns with their id, title, type, subtype, and configuration
		"""
		response = await nc._session._create_adapter().request(
			'GET', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables/{table_id}/columns",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def create_column(
		table_id: int,
		title: str,
		column_type: str,
		subtype: Optional[str] = None,
		mandatory: bool = False,
		description: Optional[str] = None,
		number_prefix: Optional[str] = None,
		number_suffix: Optional[str] = None,
		number_default: Optional[float] = None,
		number_min: Optional[float] = None,
		number_max: Optional[float] = None,
		number_decimals: Optional[int] = None,
		text_default: Optional[str] = None,
		text_max_length: Optional[int] = None,
		selection_options: Optional[str] = None,
		selection_default: Optional[str] = None,
		datetime_default: Optional[str] = None,
	):
		"""
		Create a new column in a table.

		Available column types and their subtypes:
		- "text": subtypes "line" (single line, default), "long" (multi-line), "rich" (rich text), "link" (URL)
		- "number": subtypes None (plain number, default), "stars" (rating 0-5), "progress" (percentage 0-100)
		- "selection": subtypes None (dropdown, default), "check" (checkbox), "multi" (multi-select)
		- "datetime": subtypes None (date and time, default), "date" (date only), "time" (time only)
		- "usergroup": no subtypes

		Type-specific parameters:
		- number columns: number_prefix, number_suffix, number_default, number_min, number_max, number_decimals
		- text columns: text_default, text_max_length
		- selection columns: selection_options (JSON string, e.g. '[{"id": 1, "label": "Option A"}]'), selection_default
		- datetime columns: datetime_default (ISO 8601 format)

		:param table_id: the id of the table (obtainable with list_tables)
		:param title: the column title
		:param column_type: the column type - one of "text", "number", "selection", "datetime", "usergroup"
		:param subtype: optional subtype (see above for valid values per type)
		:param mandatory: whether this column is required (default false)
		:param description: optional description of the column
		:return: the created column with its id
		"""
		payload = {
			'tableId': table_id,
			'title': title,
			'type': column_type,
			'mandatory': mandatory,
		}
		if subtype is not None:
			payload['subtype'] = subtype
		if description is not None:
			payload['description'] = description
		if number_prefix is not None:
			payload['numberPrefix'] = number_prefix
		if number_suffix is not None:
			payload['numberSuffix'] = number_suffix
		if number_default is not None:
			payload['numberDefault'] = number_default
		if number_min is not None:
			payload['numberMin'] = number_min
		if number_max is not None:
			payload['numberMax'] = number_max
		if number_decimals is not None:
			payload['numberDecimals'] = number_decimals
		if text_default is not None:
			payload['textDefault'] = text_default
		if text_max_length is not None:
			payload['textMaxLength'] = text_max_length
		if selection_options is not None:
			payload['selectionOptions'] = selection_options
		if selection_default is not None:
			payload['selectionDefault'] = selection_default
		if datetime_default is not None:
			payload['datetimeDefault'] = datetime_default
		response = await nc._session._create_adapter().request(
			'POST', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables/{table_id}/columns",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			json=payload,
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def update_column(
		column_id: int,
		title: Optional[str] = None,
		mandatory: Optional[bool] = None,
		description: Optional[str] = None,
	):
		"""
		Update a column's properties
		:param column_id: the id of the column to update (obtainable with list_columns)
		:param title: new title for the column
		:param mandatory: whether this column is required
		:param description: new description for the column
		:return: the updated column
		"""
		payload = {}
		if title is not None:
			payload['title'] = title
		if mandatory is not None:
			payload['mandatory'] = mandatory
		if description is not None:
			payload['description'] = description
		response = await nc._session._create_adapter().request(
			'PUT', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/columns/{column_id}",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			json=payload,
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def delete_column(column_id: int):
		"""
		Delete a column from a table. This also removes all data stored in this column for every row.
		:param column_id: the id of the column to delete (obtainable with list_columns)
		:return: the deleted column
		"""
		response = await nc._session._create_adapter().request(
			'DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/columns/{column_id}",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
		)
		return json.dumps(response.json())

	# --- Rows ---

	@tool
	@safe_tool
	async def list_rows(table_id: int, limit: Optional[int] = None, offset: Optional[int] = None):
		"""
		List all rows in a table with their data.
		Each row includes its id (needed for update_row/delete_row) and data as column-value pairs.
		Use list_columns first to map column IDs to column names.
		:param table_id: the id of the table (obtainable with list_tables)
		:param limit: maximum number of rows to return
		:param offset: number of rows to skip for pagination
		:return: list of rows with id, metadata, and data array of {columnId, value} pairs
		"""
		params = {}
		if limit is not None:
			params['limit'] = limit
		if offset is not None:
			params['offset'] = offset
		response = await nc._session._create_adapter().request(
			'GET', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables/{table_id}/rows",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			params=params,
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def create_row(table_id: int, data: str):
		"""
		Create a new row in a table.
		The data parameter must be a JSON object mapping column IDs to their values.
		Use list_columns first to find the column IDs for the target table.
		:param table_id: the id of the table (obtainable with list_tables)
		:param data: JSON object mapping column IDs to values, e.g. '{"1": "some text", "2": 42, "3": "2026-01-15"}'
		:return: the created row
		"""
		parsed_data = json.loads(data)
		response = await nc._session._create_adapter().request(
			'POST', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/tables/{table_id}/rows",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			json={'data': parsed_data},
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def update_row(row_id: int, data: str, view_id: Optional[int] = None):
		"""
		Update an existing row's data.
		The data parameter must be a JSON object mapping column IDs to their new values.
		Only include columns you want to change.
		:param row_id: the id of the row to update (obtainable with list_rows)
		:param data: JSON object mapping column IDs to new values, e.g. '{"1": "updated text", "3": "2026-02-20"}'
		:param view_id: optional view id for permission context
		:return: the updated row
		"""
		parsed_data = json.loads(data)
		payload = {'data': parsed_data}
		if view_id is not None:
			payload['viewId'] = view_id
		response = await nc._session._create_adapter().request(
			'PUT', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/rows/{row_id}",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
			json=payload,
		)
		return json.dumps(response.json())

	@tool
	@dangerous_tool
	async def delete_row(row_id: int):
		"""
		Delete a row from a table
		:param row_id: the id of the row to delete (obtainable with list_rows)
		:return: the deleted row
		"""
		response = await nc._session._create_adapter().request(
			'DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/tables/api/1/rows/{row_id}",
			headers={"Content-Type": "application/json", "OCS-APIREQUEST": "true"},
		)
		return json.dumps(response.json())

	return [
		list_tables,
		create_table,
		update_table,
		delete_table,
		list_columns,
		create_column,
		update_column,
		delete_column,
		list_rows,
		create_row,
		update_row,
		delete_row,
	]


def get_category_name():
	return "Tables"


async def is_available(nc: AsyncNextcloudApp):
	return 'tables' in await nc.capabilities
