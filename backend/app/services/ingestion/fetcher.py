"""Hardened document fetcher.

Every request (including robots.txt and each redirect hop):
- passes `check_url_syntax` (https, allowlisted host, default port),
- resolves the host and rejects non-public addresses,
- connects to the validated IP while sending the real Host header and TLS SNI,
  so certificates are still verified against the hostname and a second DNS
  lookup cannot change the destination,
- is paced per host, bounded by timeouts, and size-capped while streaming.
"""

from __future__ import annotations

import asyncio
import email.utils
import ipaddress
import random
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urljoin, urlunsplit

import httpx

from app.core.config import Settings
from app.services.ingestion.url_safety import (
    Resolver,
    UnsafeUrlError,
    check_url_syntax,
    resolve_public_addresses,
    system_resolver,
)

ALLOWED_CONTENT_TYPES = frozenset({"text/html", "text/markdown"})
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


class FetchErrorKind(StrEnum):
    UNSAFE_URL = "unsafe_url"
    ROBOTS_DISALLOWED = "robots_disallowed"
    HTTP_STATUS = "http_status"
    TOO_LARGE = "too_large"
    CONTENT_TYPE = "content_type"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    NETWORK = "network"
    TIMEOUT = "timeout"


class FetchError(Exception):
    def __init__(self, kind: FetchErrorKind, message: str, *, status: int | None = None):
        self.kind = kind
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: datetime
    redirects: tuple[str, ...] = field(default=())


class DocumentFetcher(Protocol):
    async def fetch(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...


@dataclass
class _RawResponse:
    status: int
    headers: httpx.Headers
    body: bytes


def _retry_after_seconds(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is None:
        return None
    delta = (parsed - datetime.now(UTC)).total_seconds()
    return min(max(delta, 0.0), MAX_RETRY_AFTER_SECONDS)


class HttpDocumentFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = system_resolver,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._allowed = settings.allowed_source_domains
        self._resolver = resolver
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=settings.fetch_connect_timeout_seconds,
                read=settings.fetch_read_timeout_seconds,
                write=settings.fetch_read_timeout_seconds,
                pool=settings.fetch_connect_timeout_seconds,
            ),
            headers={
                "User-Agent": settings.fetch_user_agent,
                "Accept": "text/html, text/markdown;q=0.9",
            },
            trust_env=False,  # never route through proxies picked up from the environment
        )
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        requested = url
        redirects: list[str] = []
        current = url
        for _ in range(self._settings.fetch_max_redirects + 1):
            parts = self._check(current)
            await self._check_robots(parts.hostname or "", current)
            response = await self._request_with_retries(current, max_bytes=self._max_bytes)
            if response.status in REDIRECT_STATUS:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(
                        FetchErrorKind.HTTP_STATUS,
                        f"redirect without Location from {current}",
                        status=response.status,
                    )
                current = urljoin(current, location)
                redirects.append(current)
                continue
            if response.status != 200:
                raise FetchError(
                    FetchErrorKind.HTTP_STATUS,
                    f"HTTP {response.status} for {current}",
                    status=response.status,
                )
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise FetchError(
                    FetchErrorKind.CONTENT_TYPE,
                    f"unsupported content type {content_type!r} for {current}",
                )
            return FetchResult(
                requested_url=requested,
                final_url=current,
                status=response.status,
                content_type=content_type,
                body=response.body,
                fetched_at=datetime.now(UTC),
                redirects=tuple(redirects),
            )
        raise FetchError(
            FetchErrorKind.TOO_MANY_REDIRECTS,
            f"more than {self._settings.fetch_max_redirects} redirects for {requested}",
        )

    @property
    def _max_bytes(self) -> int:
        return self._settings.fetch_max_response_bytes

    def _check(self, url: str):  # type: ignore[no-untyped-def]
        try:
            return check_url_syntax(url, self._allowed)
        except UnsafeUrlError as exc:
            raise FetchError(FetchErrorKind.UNSAFE_URL, f"{url}: {exc}") from exc

    async def _check_robots(self, host: str, url: str) -> None:
        parser = self._robots.get(host)
        if parser is None:
            parser = await self._load_robots(host)
            self._robots[host] = parser
        if not parser.can_fetch(self._settings.fetch_user_agent, url):
            raise FetchError(FetchErrorKind.ROBOTS_DISALLOWED, f"robots.txt disallows {url}")

    async def _load_robots(self, host: str) -> urllib.robotparser.RobotFileParser:
        parser = urllib.robotparser.RobotFileParser()
        robots_url = f"https://{host}/robots.txt"
        self._check(robots_url)
        try:
            response = await self._request_with_retries(robots_url, max_bytes=512_000)
        except FetchError as exc:
            # RFC 9309: if robots.txt is unreachable, assume everything is disallowed.
            raise FetchError(
                FetchErrorKind.ROBOTS_DISALLOWED, f"robots.txt for {host} is unavailable: {exc}"
            ) from exc
        if response.status == 200:
            parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
        elif 400 <= response.status < 500:
            parser.parse([])  # no robots.txt: no restrictions (RFC 9309 §2.3.1.3)
        else:
            raise FetchError(
                FetchErrorKind.ROBOTS_DISALLOWED,
                f"robots.txt for {host} returned HTTP {response.status}",
            )
        return parser

    async def _pace(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last_request.get(host)
            if last is not None:
                wait = self._settings.fetch_min_interval_seconds - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last_request[host] = self._clock()

    async def _request_with_retries(self, url: str, *, max_bytes: int) -> _RawResponse:
        attempts = self._settings.fetch_max_retries + 1
        for attempt in range(attempts):
            last_attempt = attempt == attempts - 1
            try:
                response = await self._request_once(url, max_bytes=max_bytes)
            except FetchError as exc:
                if exc.kind not in (FetchErrorKind.NETWORK, FetchErrorKind.TIMEOUT) or last_attempt:
                    raise
                await self._sleep(self._backoff(attempt, None))
                continue
            if response.status in RETRYABLE_STATUS and not last_attempt:
                await self._sleep(self._backoff(attempt, _retry_after_seconds(response.headers)))
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        return float(min(2**attempt, 30)) + random.uniform(0, 0.5)  # noqa: S311 (jitter)

    async def _request_once(self, url: str, *, max_bytes: int) -> _RawResponse:
        parts = self._check(url)
        host = parts.hostname or ""
        try:
            addresses = await resolve_public_addresses(host, 443, self._resolver)
        except UnsafeUrlError as exc:
            raise FetchError(FetchErrorKind.UNSAFE_URL, f"{url}: {exc}") from exc
        ip = addresses[0]
        netloc = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
        pinned = urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))

        await self._pace(host)
        try:
            async with self._client.stream(
                "GET",
                pinned,
                headers={"Host": host},
                extensions={"sni_hostname": host},
            ) as response:
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError(FetchErrorKind.TOO_LARGE, f"{url} exceeds {max_bytes} bytes")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(
                            FetchErrorKind.TOO_LARGE, f"{url} exceeds {max_bytes} bytes"
                        )
                    chunks.append(chunk)
                return _RawResponse(response.status_code, response.headers, b"".join(chunks))
        except httpx.TimeoutException as exc:
            raise FetchError(FetchErrorKind.TIMEOUT, f"timed out fetching {url}") from exc
        except httpx.TransportError as exc:
            raise FetchError(
                FetchErrorKind.NETWORK, f"network error fetching {url}: {type(exc).__name__}"
            ) from exc
