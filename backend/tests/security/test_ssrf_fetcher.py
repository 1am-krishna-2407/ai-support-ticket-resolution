from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.services.ingestion.fetcher import FetchError, FetchErrorKind, HttpDocumentFetcher

PUBLIC_IP = "104.18.0.1"
ROBOTS_OK = "User-agent: *\nDisallow: /review-mr\n"
Handler = Callable[[httpx.Request], httpx.Response]


class Harness:
    def __init__(
        self, settings: Settings, routes: dict[str, Any], dns: dict[str, list[str]] | None = None
    ):
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.routes = routes
        self.dns = dns or {"docs.gitlab.com": [PUBLIC_IP]}
        self.now = 1000.0

        async def resolver(host: str, port: int) -> list[str]:
            return self.dns.get(host, [])

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

        self.fetcher = HttpDocumentFetcher(
            settings,
            transport=httpx.MockTransport(self._handle),
            resolver=resolver,
            sleep=sleep,
            clock=lambda: self.now,
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path, httpx.Response(404))
        if callable(route):
            return route(request)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route


def html(body: str = "<main><p>ok</p></main>", **headers: str) -> httpx.Response:
    return httpx.Response(
        200, text=body, headers={"content-type": "text/html; charset=utf-8", **headers}
    )


@pytest.fixture
def fetch_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(
        allowed_source_domains=["docs.gitlab.com"],
        fetch_min_interval_seconds=1.0,
        fetch_max_retries=2,
        fetch_max_redirects=3,
        fetch_max_response_bytes=1000,
    )


def _harness(settings: Settings, **routes: Any) -> Harness:
    table = {"/robots.txt": httpx.Response(200, text=ROBOTS_OK)}
    table.update({k.replace("__", "/"): v for k, v in routes.items()})
    return Harness(settings, table)


async def test_connects_to_validated_ip_with_host_and_sni(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings, {"/robots.txt": httpx.Response(200, text=ROBOTS_OK), "/ci/": html()}
    )
    result = await h.fetcher.fetch("https://docs.gitlab.com/ci/")
    assert result.status == 200 and result.body == b"<main><p>ok</p></main>"
    page_request = h.requests[-1]
    assert page_request.url.host == PUBLIC_IP
    assert page_request.headers["host"] == "docs.gitlab.com"
    assert page_request.extensions["sni_hostname"] == "docs.gitlab.com"
    assert h.requests[0].url.path == "/robots.txt"


async def test_same_host_redirect_is_followed(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {
            "/robots.txt": httpx.Response(200, text=ROBOTS_OK),
            "/old/": httpx.Response(301, headers={"location": "/new/"}),
            "/new/": html(),
        },
    )
    result = await h.fetcher.fetch("https://docs.gitlab.com/old/")
    assert result.final_url == "https://docs.gitlab.com/new/"
    assert result.redirects == ("https://docs.gitlab.com/new/",)


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/steal",
        "http://docs.gitlab.com/ci/",
        "https://169.254.169.254/latest/meta-data/",
        "https://docs.gitlab.com:8443/",
    ],
)
async def test_unsafe_redirect_targets_are_rejected(
    fetch_settings: Settings, location: str
) -> None:
    h = Harness(
        fetch_settings,
        {
            "/robots.txt": httpx.Response(200, text=ROBOTS_OK),
            "/ci/": httpx.Response(302, headers={"location": location}),
        },
    )
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/ci/")
    assert info.value.kind is FetchErrorKind.UNSAFE_URL
    assert all(r.url.host == PUBLIC_IP for r in h.requests)


async def test_host_resolving_to_private_address_is_never_contacted(
    fetch_settings: Settings,
) -> None:
    h = Harness(fetch_settings, {}, dns={"docs.gitlab.com": ["10.0.0.7"]})
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/ci/")
    assert info.value.kind is FetchErrorKind.ROBOTS_DISALLOWED  # robots.txt fetch is refused first
    assert h.requests == []


async def test_too_many_redirects(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {
            "/robots.txt": httpx.Response(200, text=ROBOTS_OK),
            "/loop/": httpx.Response(302, headers={"location": "/loop/"}),
        },
    )
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/loop/")
    assert info.value.kind is FetchErrorKind.TOO_MANY_REDIRECTS


async def test_streamed_body_over_cap_is_rejected(fetch_settings: Settings) -> None:
    async def big_stream():  # type: ignore[no-untyped-def]
        for _ in range(20):
            yield b"x" * 100

    h = Harness(
        fetch_settings,
        {
            "/robots.txt": httpx.Response(200, text=ROBOTS_OK),
            "/big/": lambda r: httpx.Response(
                200, headers={"content-type": "text/html"}, content=big_stream()
            ),
        },
    )
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/big/")
    assert info.value.kind is FetchErrorKind.TOO_LARGE


async def test_declared_length_over_cap_is_rejected(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {"/robots.txt": httpx.Response(200, text=ROBOTS_OK), "/big/": html("y" * 5000)},
    )
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/big/")
    assert info.value.kind is FetchErrorKind.TOO_LARGE


async def test_disallowed_content_type(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {
            "/robots.txt": httpx.Response(200, text=ROBOTS_OK),
            "/file.pdf": httpx.Response(
                200, content=b"%PDF", headers={"content-type": "application/pdf"}
            ),
        },
    )
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/file.pdf")
    assert info.value.kind is FetchErrorKind.CONTENT_TYPE


async def test_retryable_status_is_retried_honouring_retry_after(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {
            "/robots.txt": httpx.Response(200, text=ROBOTS_OK),
            "/flaky/": [httpx.Response(503, headers={"retry-after": "7"}), html()],
        },
    )
    result = await h.fetcher.fetch("https://docs.gitlab.com/flaky/")
    assert result.status == 200
    assert 7.0 in h.sleeps


async def test_retries_are_bounded(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {"/robots.txt": httpx.Response(200, text=ROBOTS_OK), "/down/": httpx.Response(503)},
    )
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/down/")
    assert info.value.kind is FetchErrorKind.HTTP_STATUS and info.value.status == 503
    assert sum(1 for r in h.requests if r.url.path == "/down/") == 3  # 1 + fetch_max_retries


async def test_not_found_is_not_retried(fetch_settings: Settings) -> None:
    h = Harness(fetch_settings, {"/robots.txt": httpx.Response(200, text=ROBOTS_OK)})
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/missing/")
    assert info.value.status == 404
    assert sum(1 for r in h.requests if r.url.path == "/missing/") == 1


async def test_network_errors_are_retried_then_reported(fetch_settings: Settings) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    h = Harness(fetch_settings, {"/robots.txt": httpx.Response(200, text=ROBOTS_OK), "/x/": boom})
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/x/")
    assert info.value.kind is FetchErrorKind.NETWORK
    assert sum(1 for r in h.requests if r.url.path == "/x/") == 3


async def test_robots_disallow_is_enforced(fetch_settings: Settings) -> None:
    h = Harness(fetch_settings, {"/robots.txt": httpx.Response(200, text=ROBOTS_OK)})
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/review-mr/123/")
    assert info.value.kind is FetchErrorKind.ROBOTS_DISALLOWED
    assert [r.url.path for r in h.requests] == ["/robots.txt"]


async def test_missing_robots_allows_all(fetch_settings: Settings) -> None:
    h = Harness(fetch_settings, {"/robots.txt": httpx.Response(404), "/ci/": html()})
    assert (await h.fetcher.fetch("https://docs.gitlab.com/ci/")).status == 200


async def test_failing_robots_disallows_all(fetch_settings: Settings) -> None:
    h = Harness(fetch_settings, {"/robots.txt": httpx.Response(500), "/ci/": html()})
    with pytest.raises(FetchError) as info:
        await h.fetcher.fetch("https://docs.gitlab.com/ci/")
    assert info.value.kind is FetchErrorKind.ROBOTS_DISALLOWED


async def test_robots_is_cached_and_requests_are_paced(fetch_settings: Settings) -> None:
    h = Harness(
        fetch_settings,
        {"/robots.txt": httpx.Response(200, text=ROBOTS_OK), "/a/": html(), "/b/": html()},
    )
    await h.fetcher.fetch("https://docs.gitlab.com/a/")
    await h.fetcher.fetch("https://docs.gitlab.com/b/")
    assert [r.url.path for r in h.requests] == ["/robots.txt", "/a/", "/b/"]
    assert h.sleeps == [1.0, 1.0]  # min interval between consecutive requests to the host
