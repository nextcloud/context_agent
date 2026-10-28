# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import re
from urllib.parse import urlparse

from nc_py_api import AsyncNextcloudApp
from nc_py_api._exceptions import NextcloudException
from nc_py_api.ex_app import LogLvl
from nc_py_api.files.files_async import AsyncFilesAPI, FsNode

from ex_app.lib.logger import log

# todo: add multimodal support for the multimodal task type. That type needs a different
# maximum size.
# todo: https://github.com/nextcloud/context_agent/pull/211
TEXT_LIKE_MIMETYPE_PARTS = ('text/', 'application/json', 'application/xml', 'application/xhtml', '+xml', '+json')
MAX_FILE_SIZE = 100_000  # 100kB, approx. 25k tokens

# Match only against the path of the URL. The path must end with the file ID. Thus a
# `/f/<digits>` part in the middle of a path or in the query string is not a file link.
_FILE_ID_SUFFIX_RE = re.compile(r'/(index\.php/)?f/(\d+)/?$')

# The set of the known Nextcloud base URLs. The URLs are lowercase and have no scheme.
# The code fills this set at the first use that gets the public URL from app_api.
_nc_hosts: set[str] | None = None


def _strip_scheme(url: str) -> str:
	"""Remove the http(s):// scheme from a URL for scheme-agnostic comparison."""
	return re.sub(r'^https?://', '', url).rstrip('/')


def _split_authority_path(value: str) -> tuple[str, str]:
	"""Divide *value* into an authority and a path with `urlparse`.

	This function accepts a full URL, for example `https://host/p`. It also accepts a
	Nextcloud base entry that has no scheme, for example `host[:port][/path]`. If the
	value has no scheme, the function adds two slashes at the start. Then `urlparse`
	reads the first part as the authority and not as the path.

	The authority is `host[:port]` in lowercase. The function does not add a default
	port. Thus a value with the port `:443` or `:80` does not match a value without
	that port. This result is satisfactory, because both values come from the URLs in
	the Nextcloud configuration.

	The function compares the two hosts as exact strings. Thus an IDN host must have
	the same form (Unicode or punycode) in both values.
	"""
	parsed = urlparse(value if '://' in value else '//' + value)
	host = (parsed.hostname or '').lower()
	if not host:
		return '', ''
	authority = f'{host}:{parsed.port}' if parsed.port is not None else host
	return authority, parsed.path.rstrip('/')


def _matches_nc_base(url: str, nc_base: str) -> bool:
	"""Check if *url* is part of a known Nextcloud base. The two values can have no scheme.

	The host and the port must match exactly. Thus `cloud.example.com` does not match a
	similar host such as `cloud.example.com.evil.net` or `cloud.example.commercial.com`.
	If the Nextcloud base contains a path, the URL must be at that path or below it.
	A Nextcloud base contains a path when the server operates in a sub-path.
	"""
	nc_authority, nc_path = _split_authority_path(nc_base)
	if not nc_authority:
		return False
	url_authority, url_path = _split_authority_path(url)
	if url_authority != nc_authority:
		return False
	if not nc_path:
		return True
	return url_path == nc_path or url_path.startswith(nc_path + '/')


async def _get_nc_hosts(nc: AsyncNextcloudApp) -> set[str]:
	"""Make and cache the set of the known Nextcloud base URLs. These URLs have no scheme.

	The sources are the endpoint of the app and the `overwrite.cli.url` configuration.
	If the request to app_api fails, the result is not cached. The next call tries again.
	"""
	global _nc_hosts
	if _nc_hosts is not None:
		return _nc_hosts

	hosts = set()

	# 1. The NEXTCLOUD_URL environment variable. This is the URL of the deployment of
	# the ExApp.
	hosts.add(_strip_scheme(nc.app_cfg.endpoint).lower())

	# 2. The public URL from `overwrite.cli.url`. The absolute-URL endpoint of app_api
	# supplies this URL.
	try:
		info = await nc.ocs('GET', '/ocs/v1.php/apps/app_api/api/v1/info/nextcloud_url/absolute', params={'url': ''})
		abs_url = info.get('absolute_url', '')
		if abs_url:
			hosts.add(_strip_scheme(abs_url).lower())
	except Exception:
		await log(nc, LogLvl.WARNING, 'Could not fetch Nextcloud absolute URL from app_api, using only NEXTCLOUD_URL for internal link detection')
		return hosts

	_nc_hosts = hosts
	await log(nc, LogLvl.DEBUG, f'Known NC hosts for internal link detection: {_nc_hosts}')
	return _nc_hosts


async def is_known_nc_host(nc: AsyncNextcloudApp, url: str) -> bool:
	"""Check whether the URL points to the host of this Nextcloud instance.

	The scheme of the URL has no effect on the result.
	"""
	if not url:
		return False
	nc_hosts = await _get_nc_hosts(nc)
	return any(_matches_nc_base(url, host) for host in nc_hosts)


async def is_an_internal_file_link(nc: AsyncNextcloudApp, url: str) -> bool:
	"""Check whether the URL is an internal file link of this Nextcloud instance."""
	return bool(_FILE_ID_SUFFIX_RE.search(urlparse(url).path)) and await is_known_nc_host(nc, url)


def get_file_id_from_file_url(file_url: str) -> int:
	"""Extract the numeric file ID from a Nextcloud-internal file URL."""
	match = _FILE_ID_SUFFIX_RE.search(urlparse(file_url).path)
	if match:
		return int(match.group(2))
	raise Exception("Not a valid nextcloud file URL")


def format_fs_node(fsnode: FsNode) -> dict:
	# todo: permissions info
	return {
		'path': fsnode.user_path,
		'file_id': fsnode.info.fileid,
		'etag': fsnode.etag.replace('"', '').replace("'", ''),
		'bytes': fsnode.info.size,
		'creation_date': fsnode.info.creation_date.isoformat(),
		'last_modified': fsnode.info.last_modified.isoformat(),
		'mimetype': fsnode.info.mimetype,
		'is_shared': fsnode.is_shared,
		'is_favourite': fsnode.info.favorite,
		'is_version': fsnode.info.is_version,
		'trash_info': {
			'in_trash': fsnode.info.in_trash,
			**({
				'trashbin_filename': fsnode.info.trashbin_filename,
				'original_location': fsnode.info.trashbin_original_location,
				'deletion_time': fsnode.info.trashbin_deletion_time,
			} if fsnode.info.in_trash else {}),
		},
		'lock_info': {
			'is_locked': fsnode.lock_info.is_locked,
			**({
				'owner': fsnode.lock_info.owner,
				'owner_display_name': fsnode.lock_info.owner_display_name,
				'type': fsnode.lock_info.type.name,
				'creation_time': fsnode.lock_info.lock_creation_time,
				'ttl': fsnode.lock_info.lock_ttl,
				'locked_by_app': fsnode.lock_info.owner_editor,
			} if fsnode.lock_info.is_locked else {}),
		},
	}


async def get_file_node(nc: AsyncNextcloudApp, file_id: int) -> FsNode:
	files_handle = AsyncFilesAPI(nc._session)
	node = await files_handle.by_id(file_id)
	if not node:
		raise RuntimeError(f'No file/folder found with id: {file_id}')
	return node


async def get_file_contents(nc: AsyncNextcloudApp, fsnode: FsNode) -> str:
	"""
	RuntimeError: just return the metadata to the model since the node is one of the following:
		- a folder
		- very large in size
		- non-text mimetype
	"""
	if fsnode.is_dir:
		raise RuntimeError('Folder found at the given file id, skipping download')
	if fsnode.info.content_length > MAX_FILE_SIZE:
		raise RuntimeError(f'File id {fsnode.info.fileid} is too large to download at {fsnode.info.content_length} bytes')
	if not any(t in fsnode.info.mimetype for t in TEXT_LIKE_MIMETYPE_PARTS):
		raise RuntimeError(f'File id {fsnode.info.fileid} is of content type {fsnode.info.mimetype} so cannot be displayed as text')
	files_handle = AsyncFilesAPI(nc._session)
	return (await files_handle.download(fsnode)).decode(encoding='utf-8', errors='ignore')


async def get_file_content_from_int_link(nc: AsyncNextcloudApp, url: str) -> str:
	file_id = get_file_id_from_file_url(url)
	fsnode = await get_file_node(nc, file_id)

	try:
		return await get_file_contents(nc, fsnode)
	except RuntimeError as e:
		return f'Failed to download the file/folder: {e}.\nMore info about the node:{format_fs_node(fsnode)}'


async def count_file_tree(nc: AsyncNextcloudApp, path: str, count_limit: int) -> int:
	"""How many files and folders the node at this path holds, itself included.

	Walks the tree one level at a time and stops once ``count_limit`` is reached,
	so a deletion of a huge folder costs a few listings rather than all of them. A
	path that does not exist holds nothing.
	"""
	files_handle = AsyncFilesAPI(nc._session)
	try:
		node = await files_handle.by_path(path)
	except NextcloudException as e:
		if e.status_code == 404:
			return 0
		raise
	if node is None:
		return 0
	count = 1
	pending = [node] if node.is_dir else []
	while pending and count < count_limit:
		for child in await files_handle.listdir(pending.pop(), depth=1):
			count += 1
			if child.is_dir:
				pending.append(child)
	return min(count, count_limit)


async def trash_enabled(nc: AsyncNextcloudApp) -> bool:
	"""Whether deleted files end up in a trash bin they can be restored from."""
	files_capabilities = (await nc.capabilities).get('files') or {}
	return bool(files_capabilities.get('undelete'))
