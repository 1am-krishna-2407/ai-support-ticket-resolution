from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import DocumentNotFoundError, ErrorResponse, IngestionAlreadyRunningError


def _assert_contract(body: dict[str, Any], code: str, request_id: str) -> None:
    ErrorResponse.model_validate(body)  # exact shape
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == request_id
    assert "Traceback" not in str(body)


class _Payload(BaseModel):
    name: str = Field(max_length=5)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    from app.main import create_app

    app = create_app(settings)

    @app.get("/_test/not-found")
    async def _not_found() -> None:
        raise DocumentNotFoundError(details={"document_id": "abc"})

    @app.post("/_test/conflict")
    async def _conflict() -> None:
        raise IngestionAlreadyRunningError()

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise RuntimeError("secret internal detail sk-abcdefghijklmnopqrstuv")

    @app.post("/_test/validate")
    async def _validate(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    return app


async def test_domain_error_maps_to_contract(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/not-found")
    assert r.status_code == 404
    _assert_contract(r.json(), "DOCUMENT_NOT_FOUND", r.headers["x-request-id"])
    assert r.json()["error"]["details"] == {"document_id": "abc"}


async def test_conflict_error(client: httpx.AsyncClient) -> None:
    r = await client.post("/_test/conflict")
    assert r.status_code == 409
    _assert_contract(r.json(), "INGESTION_ALREADY_RUNNING", r.headers["x-request-id"])


async def test_unhandled_error_is_opaque_500(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/boom")
    assert r.status_code == 500
    _assert_contract(r.json(), "INTERNAL_ERROR", r.headers["x-request-id"])
    assert "secret internal detail" not in r.text
    assert "sk-" not in r.text


async def test_validation_error_does_not_echo_input(client: httpx.AsyncClient) -> None:
    r = await client.post("/_test/validate", json={"name": "way-too-long-secret-value"})
    assert r.status_code == 422
    body = r.json()
    _assert_contract(body, "INVALID_REQUEST", r.headers["x-request-id"])
    assert body["error"]["details"]["errors"][0]["loc"] == ["body", "name"]
    assert "way-too-long-secret-value" not in r.text


async def test_malformed_json_is_invalid_request(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/_test/validate", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    _assert_contract(r.json(), "INVALID_REQUEST", r.headers["x-request-id"])


async def test_unknown_route_is_not_found(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    _assert_contract(r.json(), "NOT_FOUND", r.headers["x-request-id"])


async def test_wrong_method(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/health")
    assert r.status_code == 405
    _assert_contract(r.json(), "METHOD_NOT_ALLOWED", r.headers["x-request-id"])


async def test_oversize_body_rejected_by_content_length(
    make_settings: Callable[..., Settings],
) -> None:
    from app.main import create_app

    app = create_app(make_settings(max_request_body_bytes=100))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/v1/health", content=b"x" * 101)
    assert r.status_code == 413
    _assert_contract(r.json(), "PAYLOAD_TOO_LARGE", r.headers["x-request-id"])


async def test_oversize_streamed_body_rejected(make_settings: Callable[..., Settings]) -> None:
    from app.main import create_app

    app = create_app(make_settings(max_request_body_bytes=100))

    async def body() -> Any:  # no Content-Length: chunked transfer
        for _ in range(5):
            yield b"x" * 40

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/v1/health", content=body())
    assert r.status_code == 413
    _assert_contract(r.json(), "PAYLOAD_TOO_LARGE", r.headers["x-request-id"])
