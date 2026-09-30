from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.core.config import Settings
from app.core.rate_limit import SlidingWindowLimiter, rate_limit


def test_sliding_window_allows_limit_then_reports_wait() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(2, clock=lambda: now[0])
    assert limiter.hit("a") is None
    now[0] = 10.0
    assert limiter.hit("a") is None
    assert limiter.hit("a") == pytest.approx(50.0)  # oldest hit leaves the window at t=60
    assert limiter.hit("b") is None  # budgets are per client
    now[0] = 60.5
    assert limiter.hit("a") is None


def test_key_table_is_bounded() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(1, clock=lambda: now[0], max_keys=3)
    for key in "abcdef":
        now[0] += 1
        limiter.hit(key)
    assert len(limiter._hits) <= 3


def _app(settings: Settings) -> FastAPI:
    from app.main import create_app

    app = create_app(settings)

    @app.post("/_test/limited", dependencies=[Depends(rate_limit("analyze"))])
    async def _limited() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _hits(app: FastAPI, n: int, peer: str, **headers: str) -> list[httpx.Response]:
    transport = httpx.ASGITransport(app=app, client=(peer, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return [await c.post("/_test/limited", headers=headers) for _ in range(n)]


async def test_over_limit_is_429_with_retry_after(make_settings: Callable[..., Settings]) -> None:
    app = _app(make_settings(rate_limit_analyze_per_minute=2))
    responses = await _hits(app, 3, "203.0.113.5")
    assert [r.status_code for r in responses] == [200, 200, 429]
    limited = responses[-1]
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert limited.headers["x-request-id"]


async def test_disabled_limiter_never_blocks(make_settings: Callable[..., Settings]) -> None:
    app = _app(make_settings(rate_limit_enabled=False, rate_limit_analyze_per_minute=1))
    assert all(r.status_code == 200 for r in await _hits(app, 3, "203.0.113.5"))


async def test_forwarded_for_is_ignored_from_untrusted_peers(
    make_settings: Callable[..., Settings],
) -> None:
    app = _app(make_settings(rate_limit_analyze_per_minute=1))
    first = await _hits(app, 1, "203.0.113.5", **{"X-Forwarded-For": "198.51.100.1"})
    second = await _hits(app, 1, "203.0.113.5", **{"X-Forwarded-For": "198.51.100.2"})
    assert first[0].status_code == 200 and second[0].status_code == 429  # spoofing is useless


async def test_trusted_proxy_limits_each_forwarded_client(
    make_settings: Callable[..., Settings],
) -> None:
    app = _app(make_settings(rate_limit_analyze_per_minute=1, trusted_proxy_cidrs="172.16.0.0/12"))
    proxy = "172.18.0.4"
    # nginx appends the real peer last; a client-supplied first entry must not matter.
    a = await _hits(app, 2, proxy, **{"X-Forwarded-For": "1.1.1.1, 198.51.100.1"})
    b = await _hits(app, 1, proxy, **{"X-Forwarded-For": "1.1.1.1, 198.51.100.2"})
    assert [r.status_code for r in a] == [200, 429]
    assert b[0].status_code == 200


def test_invalid_trusted_proxy_cidr_is_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        make_settings(trusted_proxy_cidrs="not-a-network")
