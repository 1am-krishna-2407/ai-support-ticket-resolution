from __future__ import annotations

from typing import Any

import pytest

from app.scripts.export_openapi import DOCS_DIR, build_spec, render

EXPECTED_ROUTES = {
    ("get", "/api/v1/health"),
    ("get", "/api/v1/version"),
    ("get", "/api/v1/ready"),
    ("post", "/api/v1/search"),
    ("post", "/api/v1/tickets/retrieve"),
    ("post", "/api/v1/tickets/analyze"),
    ("post", "/api/v1/tickets/classify"),
    ("get", "/api/v1/documents"),
    ("get", "/api/v1/documents/{document_id}"),
    ("get", "/api/v1/documents/{document_id}/chunks"),
    ("post", "/api/v1/ingestion/run"),
    ("get", "/api/v1/ingestion/status"),
    ("get", "/api/v1/ingestion/{job_id}"),
    ("post", "/api/v1/evaluations/run"),
    ("get", "/api/v1/evaluations"),
    ("get", "/api/v1/evaluations/{evaluation_id}"),
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/auth/logout"),
    ("get", "/api/v1/auth/me"),
    ("post", "/api/v1/auth/password"),
    ("get", "/api/v1/users"),
    ("post", "/api/v1/users"),
    ("patch", "/api/v1/users/{user_id}"),
    ("get", "/api/v1/tickets/history"),
    ("get", "/api/v1/tickets/history/{analysis_id}"),
    ("delete", "/api/v1/tickets/history/{analysis_id}"),
}


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return build_spec()


def _op(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    op: dict[str, Any] = spec["paths"][path][method]
    return op


def test_every_route_is_documented(spec: dict[str, Any]) -> None:
    found = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert found == EXPECTED_ROUTES


def test_errors_use_the_standard_contract(spec: dict[str, Any]) -> None:
    for method, path in EXPECTED_ROUTES:
        for status, response in _op(spec, method, path)["responses"].items():
            if status.startswith("2") or path.endswith("/ready"):
                continue
            ref = response["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorResponse"), (method, path, status)
    assert "HTTPValidationError" not in spec["components"]["schemas"]


def test_route_specific_errors(spec: dict[str, Any]) -> None:
    assert {"401", "403", "409"} <= set(_op(spec, "post", "/api/v1/ingestion/run")["responses"])
    assert "429" in _op(spec, "post", "/api/v1/tickets/analyze")["responses"]
    assert "429" in _op(spec, "post", "/api/v1/search")["responses"]
    assert "404" in _op(spec, "get", "/api/v1/documents/{document_id}")["responses"]
    assert "503" in _op(spec, "get", "/api/v1/ready")["responses"]


@pytest.mark.skipif(not DOCS_DIR.exists(), reason="docs/ is not part of this checkout")
def test_committed_snapshot_is_current(spec: dict[str, Any]) -> None:
    for name, content in render(spec).items():
        committed = (DOCS_DIR / name).read_text(encoding="utf-8")
        assert committed == content, (
            f"docs/{name} is stale: run python -m app.scripts.export_openapi"
        )
