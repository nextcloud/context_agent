# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lookups that answer who can already reach an item in Nextcloud.

Impulse radius hooks use these instead of assuming an audience from the kind of
item: a file may sit in a folder shared with a team, a table may be owned by
somebody else, a calendar may be shared out. Each lookup costs an API request or
two, which buys an answer instead of a guess.

A lookup that cannot answer raises instead of returning a small radius, because
:func:`~ex_app.lib.all_tools.lib.impulse.classify_tool_call` turns a failing hook
into the widest radius -- so an unanswerable question makes the agent ask the
user rather than act quietly.
"""
import xml.etree.ElementTree as ET

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius

# How far a share reaches, by Nextcloud share type. Types missing here fall back
# to the widest radius on purpose.
SHARE_TYPE_RADIUS = {
	0: ImpulseRadius.INDIVIDUALS,  # user
	1: ImpulseRadius.GROUP,        # group
	2: ImpulseRadius.GROUP,        # a group share's per-user instance (internal)
	3: ImpulseRadius.EXTERNAL,     # public link
	4: ImpulseRadius.EXTERNAL,     # email
	6: ImpulseRadius.EXTERNAL,     # user on a federated server
	7: ImpulseRadius.GROUP,        # team (circle)
	8: ImpulseRadius.INDIVIDUALS,  # guest account
	9: ImpulseRadius.EXTERNAL,     # group on a federated server
	10: ImpulseRadius.GROUP,       # Talk conversation
	12: ImpulseRadius.GROUP,       # Deck board
	13: ImpulseRadius.INDIVIDUALS, # a Deck share's per-user instance (internal)
	15: ImpulseRadius.EXTERNAL,    # ScienceMesh, i.e. another server
}


def share_type_radius(share_type) -> ImpulseRadius:
	"""How far a share of this type reaches."""
	try:
		share_type = int(share_type)
	except (TypeError, ValueError):
		return ImpulseRadius.EXTERNAL
	return SHARE_TYPE_RADIUS.get(share_type, ImpulseRadius.EXTERNAL)


def principal_radius(href: str) -> ImpulseRadius:
	"""How far a DAV principal reaches, e.g. 'principal:principals/groups/sales'."""
	href = (href or '').lower()
	if '/groups/' in href or '/circles/' in href or '/teams/' in href:
		return ImpulseRadius.GROUP
	if '/users/' in href:
		return ImpulseRadius.INDIVIDUALS
	return ImpulseRadius.EXTERNAL


def normalize_path(path: str) -> str:
	"""'/Projects/Q1/' -> '/Projects/Q1'"""
	return '/' + '/'.join(p for p in (path or '').split('/') if p not in ('', '.'))


def path_and_parents(path: str) -> set:
	"""Every path a share would have to cover to reach this one."""
	parts = [p for p in (path or '').split('/') if p not in ('', '.')]
	return {'/' + '/'.join(parts[:i]) for i in range(1, len(parts) + 1)}


# How far the storage a file sits on reaches, by DAV mount type. A share is not
# the only way somebody else gets to see a file, and the ones that are not shares
# leave no trace in the sharing API at all.
MOUNT_TYPE_RADIUS = {
	# A Team folder (group folder) is mounted for every group and team it is
	# assigned to, without a single share existing anywhere.
	'group': ImpulseRadius.GROUP,
	# A received share: somebody else owns the storage and sees what lands in it.
	# Only a floor -- the share lookup raises it when it can match the path to a
	# share and read its type.
	'shared': ImpulseRadius.INDIVIDUALS,
	# 'external' is deliberately absent: external storage is just as often a
	# personal mount nobody else can reach as it is a shared one, and guessing
	# either way would be worse than letting the share lookup answer.
}

MOUNT_TYPE_PROPFIND = (
	'<?xml version="1.0" encoding="UTF-8"?>'
	'<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">'
	'<d:prop><nc:mount-type/></d:prop>'
	'</d:propfind>'
)


async def mount_type_radius(nc, path) -> ImpulseRadius:
	"""How far the storage this path sits on reaches, read off its DAV mount.

	Nextcloud reports a mount type for every node, and a mount covers its whole
	subtree, so one lookup on the path answers for it and every folder above it.

	A path that does not exist yet has no mount of its own but inherits the one it
	will land in, so the closest ancestor that does exist is asked instead. That
	keeps a file written into a Team folder from looking private just because it
	is not there yet.
	"""
	user_id = await nc.user
	adapter = nc._session._create_adapter(True)
	# Deepest first, then the user's root, which always resolves.
	candidates = sorted(path_and_parents(path), key=len, reverse=True) + ['']
	for candidate in candidates:
		response = await adapter.request(
			'PROPFIND',
			f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{candidate.lstrip('/')}",
			headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "0"},
			data=MOUNT_TYPE_PROPFIND,
		)
		if response.status_code == 404:
			continue
		if response.status_code != 207:
			raise ValueError(f'Could not read the mount of {candidate!r}: HTTP {response.status_code}')
		element = ET.fromstring(response.text).find('.//{http://nextcloud.org/ns}mount-type')
		mount_type = (element.text or '').strip().lower() if element is not None else ''
		return MOUNT_TYPE_RADIUS.get(mount_type, ImpulseRadius.SELF)
	return ImpulseRadius.SELF


async def file_path_radius(nc, *paths) -> ImpulseRadius:
	"""Who can reach these files or folders, through a share on them or on a parent.

	Covers three ways in: folders the user shared out, folders that were shared
	with the user, where the owner and the other recipients see whatever is
	written into them, and Team folders, which a whole group has mounted without
	any share existing to find.
	"""
	covered = set()
	for path in paths:
		if path:
			covered |= path_and_parents(path)
	if not covered:
		raise ValueError('No path to determine the audience of')

	radius = ImpulseRadius.SELF
	for path in paths:
		if path:
			radius = max(radius, await mount_type_radius(nc, path))
	for params in ({}, {'shared_with_me': 'true'}):
		shares = await nc.ocs('GET', '/ocs/v2.php/apps/files_sharing/api/v1/shares', params=params)
		for share in shares or []:
			# 'path' is relative to the tree of whoever is asking, for shares the user
			# handed out as well as for those they received; 'file_target' is the
			# recipient's mount point and only matches for the latter.
			share_path = share.get('path') or share.get('file_target')
			if normalize_path(share_path) in covered:
				radius = max(radius, share_type_radius(share.get('share_type')))
	return radius


async def share_id_radius(nc, share_id) -> ImpulseRadius:
	"""Who an existing share already grants access to."""
	share = await nc.ocs('GET', f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}')
	if isinstance(share, list):
		share = share[0] if share else {}
	return share_type_radius(share.get('share_type'))
