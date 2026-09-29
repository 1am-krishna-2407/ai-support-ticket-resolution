from __future__ import annotations

import ipaddress

import pytest

from app.services.ingestion.url_safety import (
    UnsafeUrlError,
    check_url_syntax,
    is_public_address,
    resolve_public_addresses,
)

ALLOWED = ["docs.gitlab.com"]


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.gitlab.com/ci/runners/",
        "https://docs.gitlab.com/",
        "https://DOCS.GITLAB.COM/ci/",
        "https://docs.gitlab.com./ci/",
        "https://docs.gitlab.com:443/api/rest/",
        "https://docs.gitlab.com/ci/variables/?tab=foo#anchor",
    ],
)
def test_allowed_urls(url: str) -> None:
    check_url_syntax(url, ALLOWED)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://docs.gitlab.com/ci/", "https"),
        ("ftp://docs.gitlab.com/ci/", "https"),
        ("file:///etc/passwd", "https"),
        ("gopher://docs.gitlab.com/", "https"),
        ("javascript:alert(1)", "https"),
        ("https://evil.example.com/", "allowlist"),
        ("https://docs.gitlab.com.evil.com/", "allowlist"),
        ("https://evil.com/docs.gitlab.com", "allowlist"),
        ("https://sub.docs.gitlab.com/", "allowlist"),
        ("https://gitlab.com/", "allowlist"),
        ("https://user:pass@docs.gitlab.com/", "credentials"),
        ("https://user@docs.gitlab.com/", "credentials"),
        ("https://docs.gitlab.com@evil.com/", "credentials"),
        ("https://127.0.0.1/", "IP-literal"),
        ("https://169.254.169.254/latest/meta-data/", "IP-literal"),
        ("https://[::1]/", "IP-literal"),
        ("https://[::ffff:127.0.0.1]/", "IP-literal"),
        ("https://10.0.0.1/", "IP-literal"),
        ("https://docs.gitlab.com:8443/", "port"),
        ("https://docs.gitlab.com:22/", "port"),
        ("https://docs.gitlab.com:99999/", "parsed"),
        ("https://docs.gitlab.com/a b", "whitespace"),
        ("https://docs.gitlab.com/\r\nHost: evil", "whitespace"),
        ("https://docs.gitlab.com/\x00", "control"),
        ("https:///path-only", "no host"),
        ("https://docs.gitlab.com/" + "a" * 2100, "too long"),
    ],
)
def test_rejected_urls(url: str, reason: str) -> None:
    with pytest.raises(UnsafeUrlError, match=reason):
        check_url_syntax(url, ALLOWED)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.10.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "239.255.255.250",
        "255.255.255.255",
        "198.18.0.1",
        "192.0.0.8",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",  # AWS IPv6 metadata
        "ff02::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",
        "2002:7f00:0001::",  # 6to4 wrapping 127.0.0.1
        "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
        "2001:db8::1",  # documentation range
        "64:ff9b::808:808",  # NAT64, rejected conservatively
    ],
)
def test_non_public_addresses(address: str) -> None:
    assert not is_public_address(ipaddress.ip_address(address))


# NAT64 (64:ff9b::/96) is rejected even when it wraps a public IPv4 address: Python's
# ipaddress does not treat it as global, and refusing it is the conservative choice.
@pytest.mark.parametrize("address", ["104.18.0.1", "8.8.8.8", "2606:4700::6810:1"])
def test_public_addresses(address: str) -> None:
    assert is_public_address(ipaddress.ip_address(address))


def _resolver(answers: list[str]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return answers

    return resolve


async def test_resolution_rejects_if_any_address_is_private() -> None:
    # DNS rebinding style answer: one public and one private record.
    with pytest.raises(UnsafeUrlError, match="non-public"):
        await resolve_public_addresses(
            "docs.gitlab.com", 443, _resolver(["104.18.0.1", "10.0.0.5"])
        )


async def test_resolution_returns_public_addresses_deduplicated() -> None:
    result = await resolve_public_addresses(
        "docs.gitlab.com", 443, _resolver(["104.18.0.1", "104.18.0.1", "2606:4700::6810:1"])
    )
    assert [str(ip) for ip in result] == ["104.18.0.1", "2606:4700::6810:1"]


async def test_resolution_handles_scoped_ipv6_and_rejects_link_local() -> None:
    with pytest.raises(UnsafeUrlError):
        await resolve_public_addresses("docs.gitlab.com", 443, _resolver(["fe80::1%eth0"]))


async def test_resolution_failure_is_unsafe() -> None:
    async def failing(host: str, port: int) -> list[str]:
        raise OSError("NXDOMAIN")

    with pytest.raises(UnsafeUrlError, match="could not resolve"):
        await resolve_public_addresses("docs.gitlab.com", 443, failing)


async def test_empty_resolution_is_unsafe() -> None:
    with pytest.raises(UnsafeUrlError, match="did not resolve"):
        await resolve_public_addresses("docs.gitlab.com", 443, _resolver([]))
