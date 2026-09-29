from __future__ import annotations

import json
import logging
import re

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import JsonFormatter, request_id_var


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    from app.main import create_app

    app = create_app(settings)

    @app.get("/_test/echo-request-id")
    async def _echo() -> dict[str, str | None]:
        return {"request_id": request_id_var.get()}

    return app


async def test_request_id_generated_when_absent(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])


async def test_request_id_propagates_to_handler_context(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/echo-request-id")
    assert r.json()["request_id"] == r.headers["x-request-id"]


async def test_valid_incoming_request_id_is_honoured(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/echo-request-id", headers={"X-Request-ID": "client-abc.123"})
    assert r.headers["x-request-id"] == "client-abc.123"
    assert r.json()["request_id"] == "client-abc.123"


@pytest.mark.parametrize("bad", ["has space", "<script>", "x" * 129, "semi;colon"])
async def test_unsafe_incoming_request_id_is_replaced(client: httpx.AsyncClient, bad: str) -> None:
    r = await client.get("/api/v1/health", headers={"X-Request-ID": bad})
    assert r.headers["x-request-id"] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])


async def test_request_ids_are_unique(client: httpx.AsyncClient) -> None:
    ids = {(await client.get("/api/v1/health")).headers["x-request-id"] for _ in range(5)}
    assert len(ids) == 5


async def test_context_is_reset_after_request(client: httpx.AsyncClient) -> None:
    await client.get("/api/v1/health")
    assert request_id_var.get() is None


async def test_request_log_line_carries_request_id(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.request"):
        r = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-me"})
    records = [rec for rec in caplog.records if rec.name == "app.request"]
    assert records, "expected one request log line"
    line = json.loads(JsonFormatter().format(records[-1]))
    assert line["request_id"] == r.headers["x-request-id"] == "trace-me"
    assert line["path"] == "/api/v1/health"
    assert line["status_code"] == 200
