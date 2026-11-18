# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tools for the APIs of Nextcloud apps that have no curated tools of their own

The ocs_api_viewer app lists the apps enabled for the user and serves the OpenAPI
spec of each. Every operation in an app's default scope becomes a tool of its own,
for the apps the admin checked in the settings. Apps a curated tool module already
covers are left out, as are the core API and the administration, federation and
other scopes, whose endpoints are not meant for the user.

The settings offer a checkbox for every app detected so far. Detection runs as the
users use the agent, so an app shows up there once someone it is enabled for has,
and starts out unchecked: one tool per endpoint adds up quickly.

Reads are marked read-only. Everything else stays unmarked, so the confirmation
policy treats it as the worst case: nothing in a spec says who a call reaches or
whether it can be taken back.
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from langchain_core.tools import StructuredTool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import LogLvl, SettingsForm

from ex_app.lib.all_tools.lib.decorator import timed_memoize
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse, read_only
from ex_app.lib.logger import log

APP_STATUS_SETTING_ID = 'openapi_app_status'
# Not shown in the form: the apps detected so far, offered as checkboxes under APP_STATUS_SETTING_ID
DETECTED_APPS_SETTING_ID = 'openapi_detected_apps'

VIEWER_PATH = '/index.php/apps/ocs_api_viewer/apps'
HEADERS = {'Accept': 'application/json', 'OCS-APIRequest': 'true'}
REQUEST_TIMEOUT = 30

# The Nextcloud apps each curated tool module covers. Their endpoints get no generated tools.
CURATED_APPS = {
	'assignments': {'assistant'},
	'bookmarks': {'bookmarks'},
	# Not dav: the curated calendar tools speak CalDAV, its OCS API (out-of-office, federated calendar invitations) is left to generated tools
	'calendar': {'calendar'},
	'circles': {'circles'},
	'collectives': {'collectives'},
	'contacts': {'contacts'},
	'context_chat': {'context_chat'},
	'cookbook': {'cookbook'},
	'deck': {'deck'},
	'files': {'files'},
	'forms': {'forms'},
	'mail': {'mail'},
	'openproject': {'integration_openproject'},
	'shares': {'files_sharing'},
	'tables': {'tables'},
	'talk': {'spreed'},
}
SKIPPED_APPS = {'core', 'app_api', 'ocs_api_viewer'}.union(*CURATED_APPS.values())

HTTP_METHODS = ('get', 'post', 'put', 'patch', 'delete')
FORM_CONTENT_TYPE = 'application/x-www-form-urlencoded'
# Some model providers accept no longer tool names, and no other characters in them or in argument names
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


@dataclass
class Operation:
	"""An endpoint of an app's API, ready to be offered as a tool"""
	name: str
	description: str
	method: str
	path: str
	args_schema: dict
	# Argument name -> where it goes ('path', 'query', 'body' for a field of the body,
	# 'whole_body' for the body itself) and its name there
	arguments: dict[str, tuple[str, str]]
	# Path parameters with a default, such as the API version, which the model may leave out
	defaults: dict[str, Any] = field(default_factory=dict)
	content_type: str | None = None


# The operations of each app's spec, by app id and version. Specs only change with the app's version.
_operations: dict[tuple[str, str], list[Operation]] = {}
_known_app_ids: set[str] = set()
_settings_form: SettingsForm | None = None


def use_settings_form(form: SettingsForm):
	"""Set the form that is registered again with a checkbox for each newly detected app"""
	global _settings_form
	_settings_form = form


async def register_settings_form(nc: AsyncNextcloudApp):
	"""Register the settings form, with a checkbox for each app detected so far"""
	if _settings_form is None:
		return
	detected = _load_json_object(await nc.appconfig_ex.get_value(DETECTED_APPS_SETTING_ID, default='{}'))
	for settings_field in _settings_form.fields:
		if settings_field.id == APP_STATUS_SETTING_ID:
			apps = sorted(detected.items(), key=lambda app: str(app[1]).lower())
			settings_field.options = {f'{name} ({app_id})': app_id for app_id, name in apps}
			settings_field.default = dict.fromkeys(detected, False)
	await nc.ui.settings.register_form(_settings_form)


async def _remember_detected_apps(nc: AsyncNextcloudApp, apps: list[dict]):
	if all(app['id'] in _known_app_ids for app in apps):
		return
	detected = _load_json_object(await nc.appconfig_ex.get_value(DETECTED_APPS_SETTING_ID, default='{}'))
	new = {app['id']: app.get('name') or app['id'] for app in apps if app['id'] not in detected}
	if new:
		detected.update(new)
		await nc.appconfig_ex.set_value(DETECTED_APPS_SETTING_ID, json.dumps(detected))
		await register_settings_form(nc)
	_known_app_ids.update(detected)


def _load_json_object(value) -> dict:
	try:
		loaded = json.loads(value or '{}')
	except (TypeError, ValueError):
		return {}
	return loaded if isinstance(loaded, dict) else {}


async def _viewer_request(nc: AsyncNextcloudApp, path: str = ''):
	response = await asyncio.wait_for(
		nc._session._create_adapter().request('GET', f'{nc.app_cfg.endpoint}{VIEWER_PATH}{path}', headers=HEADERS),
		timeout=REQUEST_TIMEOUT,
	)
	if response.status_code != 200:
		raise ValueError(f'ocs_api_viewer answered {VIEWER_PATH}{path} with HTTP {response.status_code}')
	return response.json()


@timed_memoize(1*60)
async def list_detected_apps(nc: AsyncNextcloudApp) -> list[dict]:
	"""The apps enabled for the user that have a spec of their default scope and no curated tools"""
	apps = await _viewer_request(nc)
	return [
		app for app in apps
		if isinstance(app, dict) and app.get('id') not in SKIPPED_APPS
		# An app's default scope is listed under the app's own id, the others as "<app>-<scope>"
		and app.get('id') in (app.get('specs') or [])
	]


async def get_operations(nc: AsyncNextcloudApp, app: dict) -> list[Operation]:
	key = (app['id'], str(app.get('version')))
	if key not in _operations:
		spec = await _viewer_request(nc, '/' + quote(app['id'], safe=''))
		operations = build_operations(app['id'], app.get('name') or app['id'], spec)
		# Drop the operations of the versions this one replaces
		for stale in [k for k in _operations if k[0] == app['id']]:
			del _operations[stale]
		_operations[key] = operations
	return _operations[key]


def build_operations(app_id: str, app_name: str, spec: dict) -> list[Operation]:
	operations = []
	names = set()
	for path, path_item in (spec.get('paths') or {}).items():
		if not isinstance(path_item, dict):
			continue
		for method in HTTP_METHODS:
			operation = path_item.get(method)
			if not isinstance(operation, dict):
				continue
			built = build_operation(app_id, app_name, path, method, path_item, operation, spec)
			if built is None:
				continue
			built.name = _unique_name(built.name, names)
			names.add(built.name)
			operations.append(built)
	return operations


def build_operation(app_id, app_name, path, method, path_item, operation, spec) -> Operation | None:
	"""Turn an operation of the spec into a tool's schema, or None if it cannot be offered"""
	if operation.get('deprecated'):
		return None

	properties = {}
	required = []
	arguments = {}
	defaults = {}

	# Parameters of the operation override those of its path with the same name and location
	parameters = {}
	for parameter in (path_item.get('parameters') or []) + (operation.get('parameters') or []):
		parameter = resolve_refs(parameter, spec)
		if isinstance(parameter, dict) and 'name' in parameter:
			parameters[(parameter.get('in'), parameter['name'])] = parameter
	for (location, name), parameter in parameters.items():
		# Headers are ours to send (OCS-APIRequest), cookies are not used
		if location not in ('path', 'query'):
			continue
		argument = _argument_name(name)
		if argument in properties:
			return None
		schema = dict(parameter.get('schema') or {})
		if parameter.get('description') and 'description' not in schema:
			schema['description'] = parameter['description']
		properties[argument] = schema
		arguments[argument] = (location, name)
		default = _default(schema)
		if location == 'path' and default is not None:
			# Typically the API version, which has a single value anyway
			defaults[argument] = default
		elif location == 'path' or parameter.get('required'):
			required.append(argument)

	content_type = None
	request_body = resolve_refs(operation.get('requestBody'), spec)
	if isinstance(request_body, dict) and request_body.get('content'):
		content = request_body['content']
		content_type = next(
			(t for t in content if t == 'application/json'),
			next((t for t in content if 'json' in t or t == FORM_CONTENT_TYPE), None),
		)
		# Uploads and other binary bodies are left to the curated tools
		if content_type is None:
			return None
		body_schema = (content[content_type] or {}).get('schema') or {}
		body_properties = body_schema.get('properties')
		flatten = (
			isinstance(body_properties, dict) and body_properties
			and all(_argument_name(name) not in properties for name in body_properties)
			and len({_argument_name(name) for name in body_properties}) == len(body_properties)
		)
		if flatten:
			for name, schema in body_properties.items():
				argument = _argument_name(name)
				properties[argument] = schema
				arguments[argument] = ('body', name)
				if name in (body_schema.get('required') or []):
					required.append(argument)
		elif 'body' not in properties:
			properties['body'] = body_schema
			arguments['body'] = ('whole_body', 'body')
			if request_body.get('required'):
				required.append('body')
		else:
			return None

	operation_id = operation.get('operationId') or f'{method}_{path}'
	summary = '\n'.join(text for text in (operation.get('summary'), operation.get('description')) if text)
	description = f'{app_name}: {summary.strip() or method.upper() + " " + path}'

	return Operation(
		name=_tool_name(f'{app_id}_{operation_id}'),
		description=description[:MAX_DESCRIPTION_LENGTH],
		method=method,
		path=path,
		args_schema={'type': 'object', 'properties': properties, 'required': required},
		arguments=arguments,
		defaults=defaults,
		content_type=content_type,
	)


def resolve_refs(node, spec: dict, seen: tuple = ()):
	"""Inline the $refs to the spec's components, which the model cannot follow"""
	if isinstance(node, list):
		return [resolve_refs(item, spec, seen) for item in node]
	if not isinstance(node, dict):
		return node
	ref = node.get('$ref')
	if isinstance(ref, str):
		# A schema that contains itself is cut off where it recurs
		if ref in seen or not ref.startswith('#/'):
			return {'type': 'object'}
		target = spec
		for part in ref[2:].split('/'):
			part = part.replace('~1', '/').replace('~0', '~')
			target = target.get(part) if isinstance(target, dict) else None
		resolved = resolve_refs(target if isinstance(target, dict) else {}, spec, seen + (ref,))
		siblings = {key: resolve_refs(value, spec, seen) for key, value in node.items() if key != '$ref'}
		return {**resolved, **siblings}
	return {key: resolve_refs(value, spec, seen) for key, value in node.items()}


def _default(schema: dict):
	if 'default' in schema:
		return schema['default']
	enum = schema.get('enum')
	if isinstance(enum, list) and len(enum) == 1:
		return enum[0]
	return None


def _argument_name(name: str) -> str:
	# PHP takes lists as "ids[]"
	name = name.removesuffix('[]')
	return re.sub(r'[^a-zA-Z0-9_.-]', '_', name)[:MAX_NAME_LENGTH] or '_'


def _tool_name(name: str) -> str:
	name = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
	return name[:MAX_NAME_LENGTH]


def _unique_name(name: str, taken: set[str]) -> str:
	unique = name
	counter = 2
	while unique in taken:
		suffix = f'_{counter}'
		unique = name[:MAX_NAME_LENGTH - len(suffix)] + suffix
		counter += 1
	return unique


def _query_value(value):
	if isinstance(value, bool):
		return 'true' if value else 'false'
	if isinstance(value, list):
		return [_query_value(item) for item in value]
	if isinstance(value, dict):
		return json.dumps(value)
	return value


async def call_operation(nc: AsyncNextcloudApp, origin: str, operation: Operation, tool_args: dict) -> str:
	path = operation.path
	query = {}
	body = None
	values = {**operation.defaults, **{key: value for key, value in tool_args.items() if value is not None}}
	for argument, value in values.items():
		# Arguments the spec does not know are left out
		location, name = operation.arguments.get(argument, (None, None))
		if location == 'path':
			path = path.replace('{' + name + '}', quote(str(value), safe=''))
		elif location == 'query':
			query[name] = _query_value(value)
		elif location == 'body':
			body = body if isinstance(body, dict) else {}
			body[name] = value
		elif location == 'whole_body':
			body = value
	if '/ocs/v' in operation.path:
		query.setdefault('format', 'json')

	payload = {}
	if body is not None:
		payload = {'data': body} if operation.content_type == FORM_CONTENT_TYPE else {'json': body}
	method = operation.method.upper()
	# The spec's paths already start with the web root of the instance
	response = await nc._session._create_adapter().request(
		method, origin + path, headers=HEADERS, params=query, **payload,
	)
	try:
		result = response.json()
	except ValueError:
		result = None

	if response.status_code >= 400:
		message = None
		if isinstance(result, dict):
			meta = result.get('ocs', {}).get('meta', {}) if isinstance(result.get('ocs'), dict) else {}
			message = meta.get('message') or result.get('message') or result.get('error')
		raise ValueError(f'{method} {path} failed with HTTP {response.status_code}: {message or response.reason}')

	if result is None:
		content_type = response.headers.get('Content-Type', '')
		if content_type.startswith('text/'):
			return response.text
		return f'The response is {content_type or "binary data"} of {len(response.content or b"")} bytes, which cannot be shown.'
	# Pass on what the endpoint returned, without the OCS envelope around it
	if isinstance(result, dict) and isinstance(result.get('ocs'), dict) and 'data' in result['ocs']:
		result = result['ocs']['data']
	return json.dumps(result)


def make_tool(nc: AsyncNextcloudApp, origin: str, operation: Operation) -> StructuredTool:
	async def call(**tool_args):
		return await call_operation(nc, origin, operation, tool_args)

	if operation.method == 'get':
		read_only(impulse(ImpulseRadius.SELF)(call))
	return StructuredTool(
		name=operation.name,
		description=operation.description,
		args_schema=operation.args_schema,
		coroutine=call,
	)


async def get_tools(nc: AsyncNextcloudApp):
	apps = await list_detected_apps(nc)
	await _remember_detected_apps(nc, apps)
	activated = _load_json_object(await nc.appconfig_ex.get_value(APP_STATUS_SETTING_ID, default='{}'))
	apps = [app for app in apps if activated.get(app['id'], False)]

	results = await asyncio.gather(*(get_operations(nc, app) for app in apps), return_exceptions=True)
	endpoint = urlsplit(nc.app_cfg.endpoint)
	origin = f'{endpoint.scheme}://{endpoint.netloc}'
	tools = []
	for app, operations in zip(apps, results):
		if isinstance(operations, BaseException):
			await log(nc, LogLvl.WARNING, f'Failed to load the API spec of {app["id"]}: {operations!r}')
			continue
		tools.extend(make_tool(nc, origin, operation) for operation in operations)
	return tools


def get_category_name():
	return 'Auto-detected app APIs'


async def is_available(nc: AsyncNextcloudApp):
	try:
		await list_detected_apps(nc)
	except Exception:
		return False
	return True
