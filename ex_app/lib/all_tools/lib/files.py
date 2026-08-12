# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import re

from nc_py_api import AsyncNextcloudApp
from nc_py_api.files.files_async import AsyncFilesAPI, FsNode

TEXT_LIKE_MIMETYPE_PARTS = ('text/', 'application/json', 'application/xml', 'application/xhtml', '+xml', '+json')
MAX_FILE_SIZE = 100_000  # 100kB, approx. 25k tokens

_FILE_ID_SUFFIX_RE = re.compile(r'/(index\.php/)?f/(\d+)/?$')


def _strip_scheme(url: str) -> str:
	"""Remove the http(s):// scheme from a URL for scheme-agnostic comparison."""
	return re.sub(r'^https?://', '', url)


def is_an_internal_file_link(nc: AsyncNextcloudApp, url: str) -> bool:
	"""Check whether a URL is an internal file link for this Nextcloud instance (scheme-agnostic)."""
	nc_host = _strip_scheme(nc.app_cfg.endpoint.rstrip('/'))
	url_without_scheme = _strip_scheme(url)
	return url_without_scheme.startswith(nc_host) and bool(_FILE_ID_SUFFIX_RE.search(url_without_scheme[len(nc_host):]))


def get_file_id_from_file_url(file_url: str) -> int:
	"""Extract the numeric file ID from a Nextcloud-internal file URL."""
	match = _FILE_ID_SUFFIX_RE.search(file_url)
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
