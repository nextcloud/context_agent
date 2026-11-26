# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from nc_py_api import AsyncNextcloudApp

# nc_py_api has no public way to send a request that is neither OCS nor one of its own
# APIs. So this goes through the sessions it keeps for itself, instead of making a new
# one per request, each with its own connection pool that needs closing.
# The session is looked up on each call, as nc_py_api may replace it.


async def nc_request(nc: AsyncNextcloudApp, method: str, url: str, **kwargs):
	"""Send a request to Nextcloud as the user of nc"""
	return await nc._session.adapter.request(method, url, **kwargs)


async def nc_dav_request(nc: AsyncNextcloudApp, method: str, url: str, **kwargs):
	"""Send a DAV request to Nextcloud as the user of nc. A relative url is relative to the DAV endpoint."""
	return await nc._session.adapter_dav.request(method, url, **kwargs)
