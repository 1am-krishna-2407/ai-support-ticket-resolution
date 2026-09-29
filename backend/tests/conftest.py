from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings

TEST_DB_URL_PLACEHOLDER = "postgresql+asyncpg://user:pass@localhost:5432/unused"


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Build Settings from explicit values only (ignores the developer's .env)."""

    def _make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": TEST_DB_URL_PLACEHOLDER,
            "admin_mode": "token",
            "admin_token": "test-admin-token-0123456789",
            "environment": "test",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[call-arg]

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=False lets us observe the 500 response the app actually sends.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
