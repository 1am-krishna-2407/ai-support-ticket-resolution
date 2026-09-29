"""URL and address validation for outbound fetches (SSRF defence).

Two layers:
1. `check_url_syntax`: https only, allowlisted hostname, no credentials, no IP
   literals, default port only.
2. `resolve_public_addresses`: resolves the hostname and rejects it if *any*
   resolved address is not publicly routable (loopback, private, link-local,
   cloud metadata, CGNAT, multicast, reserved, or IPv6 forms that embed one).

The fetcher connects to an address returned by (2), so a DNS answer that changes
between check and connect (rebinding) cannot redirect the request.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import SplitResult, urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_URL_LENGTH = 2048
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "169.254.0.0/16",  # link-local, incl. 169.254.169.254 metadata
        "192.0.0.0/24",
        "198.18.0.0/15",
        "fd00:ec2::/32",  # AWS IPv6 metadata lives in fd00:ec2::254
    )
)


class UnsafeUrlError(ValueError):
    """The URL or its resolved addresses are not allowed for outbound fetches."""


def check_url_syntax(url: str, allowed_domains: Iterable[str]) -> SplitResult:
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError("URL is too long")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise UnsafeUrlError("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"URL could not be parsed: {exc}") from exc
    if parts.scheme != "https":
        raise UnsafeUrlError("only https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("URLs with credentials are not allowed")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeUrlError("URL has no host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UnsafeUrlError("IP-literal hosts are not allowed")
    if port not in (None, 443):
        raise UnsafeUrlError("only the default https port is allowed")
    if host not in {d.lower() for d in allowed_domains}:
        raise UnsafeUrlError(f"host {host!r} is not in the allowlist")
    return parts


def is_public_address(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded: IPAddress | None = ip.ipv4_mapped or ip.sixtofour
        if ip.teredo:
            embedded = ip.teredo[1]
        if ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None and not is_public_address(embedded):
            return False
    if any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def resolve_public_addresses(
    host: str, port: int = 443, resolver: Resolver = system_resolver
) -> list[IPAddress]:
    try:
        raw = await resolver(host, port)
    except OSError as exc:
        raise UnsafeUrlError(f"could not resolve {host!r}") from exc
    addresses: list[IPAddress] = []
    for value in raw:
        # getaddrinfo may return scoped IPv6 addresses such as "fe80::1%eth0".
        ip = ipaddress.ip_address(value.split("%", 1)[0])
        if not is_public_address(ip):
            raise UnsafeUrlError(f"{host!r} resolves to a non-public address")
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise UnsafeUrlError(f"{host!r} did not resolve to any address")
    return addresses
