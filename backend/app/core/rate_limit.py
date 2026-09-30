"""Per-client rate limiting for the expensive public routes (analysis and search).

In-process sliding windows: one budget per client per route group, reset on restart.
The platform runs as a single backend process (ADR D1), so no shared store is needed.
Behind the frontend's nginx proxy every request arrives from the proxy's address; the
client is then taken from the last X-Forwarded-For entry (the one nginx appended), but
only when the peer is inside TRUSTED_PROXY_CIDRS. Otherwise the header is ignored, so a
client cannot choose its own key.
"""

from __future__ import annotations

import ipaddress
import math
import time
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import RateLimitedError

RateGroup = Literal["analyze", "search"]
WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        *,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Record a request; return seconds until retry when over the limit, else None."""
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self._max_keys:
                self._evict(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(hits[0] + self._window - now, 0.0)
        hits.append(now)
        return None

    def _evict(self, now: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self._window]
        for key in stale:
            del self._hits[key]
        if len(self._hits) >= self._max_keys:  # all active: drop the oldest-touched
            oldest = min(self._hits, key=lambda k: self._hits[k][-1])
            del self._hits[oldest]


def build_limiters(settings: Settings) -> dict[RateGroup, SlidingWindowLimiter]:
    return {
        "analyze": SlidingWindowLimiter(settings.rate_limit_analyze_per_minute),
        "search": SlidingWindowLimiter(settings.rate_limit_search_per_minute),
    }


def client_key(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _trusted(peer, settings.trusted_proxy_cidrs):
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return peer


def _trusted(peer: str, cidrs: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in cidrs)


def rate_limit(group: RateGroup) -> Callable[..., Coroutine[Any, Any, None]]:
    """FastAPI dependency factory: 429 RATE_LIMITED with Retry-After when over budget."""

    async def dependency(
        request: Request, settings: Annotated[Settings, Depends(get_settings)]
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        limiters: dict[RateGroup, SlidingWindowLimiter] = request.app.state.rate_limiters
        retry_after = limiters[group].hit(client_key(request, settings))
        if retry_after is not None:
            seconds = max(1, math.ceil(retry_after))
            raise RateLimitedError(
                "Too many requests. Please retry later.",
                details={"retry_after_seconds": seconds, "group": group},
                headers={"Retry-After": str(seconds)},
            )

    return dependency
