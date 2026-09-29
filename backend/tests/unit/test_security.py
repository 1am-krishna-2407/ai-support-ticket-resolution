from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.core.config import Settings
from app.core.security import require_admin

TOKEN = "test-admin-token-0123456789"


def _app(settings: Settings) -> FastAPI:
    from app.main import create_app

    app = create_app(settings)

    @app.post("/_test/admin", dependencies=[Depends(require_admin)])
    async def _admin() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _client(app: FastAPI, client_host: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
def build(make_settings: Callable[..., Settings]) -> Callable[..., FastAPI]:
    def _build(**overrides: object) -> FastAPI:
        return _app(make_settings(**overrides))

    return _build


async def _post(app: FastAPI, host: str = "203.0.113.5", **headers: str) -> httpx.Response:
    async for c in _client(app, host):
        return await c.post("/_test/admin", headers=headers)
    raise AssertionError("unreachable")


async def test_token_mode_missing_header_is_unauthorized(build: Callable[..., FastAPI]) -> None:
    r = await _post(build(admin_mode="token", admin_token=TOKEN))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_token_mode_wrong_token_is_forbidden(build: Callable[..., FastAPI]) -> None:
    r = await _post(build(admin_mode="token", admin_token=TOKEN), **{"X-Admin-Token": "nope"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_token_mode_correct_token_allowed(build: Callable[..., FastAPI]) -> None:
    r = await _post(build(admin_mode="token", admin_token=TOKEN), **{"X-Admin-Token": TOKEN})
    assert r.status_code == 200


async def test_local_mode_rejects_remote_clients(build: Callable[..., FastAPI]) -> None:
    r = await _post(build(admin_mode="local", admin_token=None), host="203.0.113.5")
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
async def test_local_mode_allows_loopback(build: Callable[..., FastAPI], host: str) -> None:
    r = await _post(build(admin_mode="local", admin_token=None), host=host)
    assert r.status_code == 200


async def test_local_mode_does_not_trust_forwarded_headers(build: Callable[..., FastAPI]) -> None:
    r = await _post(
        build(admin_mode="local", admin_token=None),
        host="203.0.113.5",
        **{"X-Forwarded-For": "127.0.0.1"},
    )
    assert r.status_code == 403


async def test_dev_mode_allows_everything(build: Callable[..., FastAPI]) -> None:
    r = await _post(build(admin_mode="dev", admin_token=None))
    assert r.status_code == 200
