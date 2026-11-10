# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SSRF protection helpers for the `web_fetch` tool.

The function :func:`assert_public_url` refuses these URLs:
- A URL that does not use the HTTP or the HTTPS scheme.
- A URL with a host that resolves to a loopback, a private, a link-local,
  a reserved, or a different non-public IP address. This includes the cloud
  metadata address `169.254.169.254` and the mDNS names that end with `.local`.

The class :class:`PublicAddressResolver` applies the same address check when the
HTTP client connects.

This is a hard limit. It does not change with the content of the conversation.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import typing
from urllib.parse import urlsplit

from niquests.packages.urllib3.contrib.resolver._async.system import SystemResolver


class BlockedUrlError(Exception):
	"""The SSRF guard raises this error when it refuses a URL.

	It is safe to show the message of this error to the agent.
	"""


# These ranges are not globally routable.
_EXTRA_BLOCKED_NETWORKS = tuple(
	ipaddress.ip_network(cidr)
	for cidr in (
		'100.64.0.0/10',       # RFC 6598 CGNAT and shared address space
		'192.0.0.0/24',        # RFC 6890 IETF protocol assignments
		'192.0.2.0/24',        # TEST-NET-1
		'198.18.0.0/15',       # benchmark tests
		'198.51.100.0/24',     # TEST-NET-2
		'203.0.113.0/24',      # TEST-NET-3
		'192.88.99.0/24',      # 6to4 relay anycast
		'64:ff9b::/96',        # the well-known NAT64 prefix
		'100::/64',            # IPv6 discard only
	)
)


def _is_public_ip(ip: str) -> bool:
	try:
		addr = ipaddress.ip_address(ip)
	except ValueError:
		return False

	if (
		addr.is_private
		or addr.is_loopback
		or addr.is_link_local
		or addr.is_multicast
		or addr.is_reserved
		or addr.is_unspecified
	):
		return False

	# Use the global-reachability flag of the standard library if it is available. The
	# meaning of this flag changes between Python versions. Thus do not use it alone.
	if getattr(addr, 'is_global', True) is False:
		return False

	for network in _EXTRA_BLOCKED_NETWORKS:
		if addr.version == network.version and addr in network:
			return False

	# An IPv4-mapped, a 6to4, or a Teredo address can hide a private IPv4 target.
	# Extract the IPv4 addresses and examine them again.
	if isinstance(addr, ipaddress.IPv6Address):
		embedded = [addr.ipv4_mapped, addr.sixtofour, *(addr.teredo or ())]
		return all(_is_public_ip(str(ip)) for ip in embedded if ip is not None)

	return True


async def _resolve_all(host: str) -> list[str]:
	"""Find all A and AAAA addresses of a host name.

	`socket.getaddrinfo` stops its thread until the DNS reply comes, and a hostile
	server can delay that reply. Thus use `loop.getaddrinfo`, which does the same work
	in a thread pool. The other tasks then continue. But the pool has a limited number
	of threads, and it is not possible to cancel the call.
	"""
	loop = asyncio.get_running_loop()
	infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
	return [info[4][0] for info in infos]


class PublicAddressResolver(SystemResolver):
	"""A niquests resolver that refuses the non-public addresses at connection time.

	:func:`assert_public_url` resolves the host before the request. The HTTP client
	then resolves the host again when it connects. A hostile DNS server can send a
	public address for the first lookup and an internal address for the second lookup
	(DNS rebinding). This resolver does the lookup for the connection itself. Thus the
	client connects only to the addresses that this resolver examined.

	The hosts in *allowed_hosts* skip the check. Use this for the hosts of this
	Nextcloud instance, which can have an internal address.
	"""

	def __init__(self, allowed_hosts: typing.Iterable[str] = ()):
		super().__init__()
		self.allowed_hosts = {host.lower() for host in allowed_hosts}

	async def getaddrinfo(self, host, port, family, type, proto=0, flags=0, *, quic_upgrade_via_dns_rr=False):
		records = await super().getaddrinfo(
			host, port, family, type, proto, flags, quic_upgrade_via_dns_rr=quic_upgrade_via_dns_rr,
		)
		name = host.decode('ascii') if isinstance(host, bytes) else (host or '')
		if name.lower() in self.allowed_hosts:
			return records
		for record in records:
			if not _is_public_ip(record[4][0]):
				raise socket.gaierror(f"refused: '{name}' resolves to a non-public address")
		return records


async def assert_public_url(url: str) -> None:
	"""Raise :class:`BlockedUrlError` if *url* is not an HTTP or HTTPS URL to a public host.

	All the addresses of the host must be public. If one address of the host is
	internal, this function refuses the URL.

	This check alone does not prevent DNS rebinding, because the HTTP client resolves
	the host again. Send the request with :class:`PublicAddressResolver` for that.
	"""
	parts = urlsplit(url)
	if parts.scheme not in ('http', 'https'):
		raise BlockedUrlError(f"(refused: only http(s) URLs are allowed, got '{parts.scheme or 'no scheme'}')")

	host = (parts.hostname or '').strip()
	if not host:
		raise BlockedUrlError('(refused: URL has no host)')

	# Refuse all mDNS and local names. These names resolve only on the local network.
	if (
		host == 'localhost'
		or host.endswith('.local')
		or host.endswith('.localhost')
		or host.endswith('.internal')
		or host.endswith('.lan')
	):
		raise BlockedUrlError(f"(refused: '{host}' is a local/internal hostname)")

	# If the host is an IP address, check it directly.
	try:
		ipaddress.ip_address(host)
		is_literal_ip = True
	except ValueError:
		is_literal_ip = False

	if is_literal_ip:
		if not _is_public_ip(host):
			raise BlockedUrlError(f"(refused: '{host}' is a non-public IP address)")
		return

	try:
		resolved = await _resolve_all(host)
	except socket.gaierror:
		raise BlockedUrlError(f"(refused: could not resolve host '{host}')")

	if not resolved:
		raise BlockedUrlError(f"(refused: host '{host}' did not resolve to any address)")

	for ip in resolved:
		if not _is_public_ip(ip):
			raise BlockedUrlError(f"(refused: '{host}' resolves to a non-public address)")
