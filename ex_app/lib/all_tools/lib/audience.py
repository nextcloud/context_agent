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


async def file_path_radius(nc, *paths) -> ImpulseRadius:
	"""Who can reach these files or folders, through a share on them or on a parent.

	Covers both directions: folders the user shared out, and folders that were
	shared with the user, where the owner and the other recipients see whatever is
	written into them.
	"""
	covered = set()
	for path in paths:
		if path:
			covered |= path_and_parents(path)
	if not covered:
		raise ValueError('No path to determine the audience of')

	radius = ImpulseRadius.SELF
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
