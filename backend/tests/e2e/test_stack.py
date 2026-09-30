"""End to end against the running Compose stack, through the frontend's nginx proxy.

    E2E_BASE_URL=http://127.0.0.1:8080 pytest tests/e2e             # no LLM calls
    E2E_BASE_URL=... E2E_LLM=1 pytest tests/e2e                      # also analyses a ticket

Covers the real path: nginx (static SPA + /api proxy) → FastAPI → Postgres/pgvector →
the configured embedding provider (one query embedding per search). With E2E_LLM=1 the
analysis test also calls the configured LLM (two to three calls). Skipped when
E2E_BASE_URL is unset. Nothing here needs or sends the admin token.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not set (needs the Compose stack)"),
]
API = "/api/v1"


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL, timeout=600, trust_env=False) as c:
        yield c


def _ok(response: httpx.Response, status: int = 200) -> dict:  # type: ignore[type-arg]
    assert response.status_code == status, response.text[:500]
    assert response.headers.get("x-request-id")
    return response.json()  # type: ignore[no-any-return]


def test_frontend_serves_the_app_and_spa_routes(client: httpx.Client) -> None:
    for path in ("/", "/tickets", "/knowledge-base", "/evaluation"):
        r = client.get(path)
        assert r.status_code == 200 and '<div id="root">' in r.text, path


def test_health_version_and_readiness(client: httpx.Client) -> None:
    assert _ok(client.get(f"{API}/health"))["status"] == "ok"
    assert _ok(client.get(f"{API}/version"))["api_version"]
    ready = _ok(client.get(f"{API}/ready"))
    checks = {c["name"]: c["status"] for c in ready["checks"]}
    assert checks["database"] == "ok" and checks["vector_search"] == "ok"
    assert ready["knowledge_base"]["documents"] > 0
    assert ready["knowledge_base"]["embedded_chunks"] == ready["knowledge_base"]["chunks"]


@pytest.mark.parametrize("method", ["hybrid", "vector", "lexical"])
def test_search_finds_real_evidence(client: httpx.Client, method: str) -> None:
    body = _ok(
        client.post(
            f"{API}/search",
            json={"query": "x509: certificate signed by unknown authority", "method": method},
        )
    )
    assert body["evidence_sufficient"] is True and body["results"]
    top = body["results"][0]
    assert top["url"].startswith("https://docs.gitlab.com/") and top["passes_threshold"]
    assert {"semantic", "lexical", "fused", "final"} <= set(top["scores"])


def test_out_of_scope_search_reports_insufficient_evidence(client: httpx.Client) -> None:
    body = _ok(
        client.post(
            f"{API}/search", json={"query": "printer paper jam on the office laser printer"}
        )
    )
    assert body["evidence_sufficient"] is False
    assert "below the threshold" in body["reason"]


def test_documents_detail_and_chunks(client: httpx.Client) -> None:
    page = _ok(client.get(f"{API}/documents", params={"limit": 5}))
    assert page["total"] > 0 and page["items"]
    document_id = page["items"][0]["document_id"]
    detail = _ok(client.get(f"{API}/documents/{document_id}"))
    assert detail["document_id"] == document_id
    chunks = _ok(client.get(f"{API}/documents/{document_id}/chunks", params={"limit": 3}))
    assert chunks["total"] > 0 and chunks["items"]


def test_errors_follow_the_contract(client: httpx.Client) -> None:
    missing = client.get(f"{API}/documents/no-such-document")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    invalid = client.post(f"{API}/search", json={"query": ""})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_REQUEST"


def test_admin_routes_reject_missing_and_wrong_tokens(client: httpx.Client) -> None:
    assert client.post(f"{API}/ingestion/run", json={}).status_code == 401
    wrong = client.post(
        f"{API}/evaluations/run", json={}, headers={"X-Admin-Token": "wrong-token-000000"}
    )
    assert wrong.status_code == 403


def test_status_and_history_endpoints(client: httpx.Client) -> None:
    assert _ok(client.get(f"{API}/ingestion/status"))
    runs = _ok(client.get(f"{API}/evaluations"))
    assert "items" in runs


@pytest.mark.skipif(os.environ.get("E2E_LLM") != "1", reason="set E2E_LLM=1 to spend LLM calls")
def test_ticket_analysis_returns_a_validated_draft(client: httpx.Client) -> None:
    body = _ok(
        client.post(
            f"{API}/tickets/analyze",
            json={
                "title": "Webhook deliveries fail with 'Hook execution failed'",
                "description": "Project webhooks to our CI endpoint stopped working. The webhook "
                "log shows 'Hook execution failed: execution expired' for every event.",
            },
        )
    )
    assert body["label"].startswith("AI-generated draft")
    assert body["resolution"]["status"] in {
        "draft",
        "withheld",
        "insufficient_evidence",
        "unavailable",
    }
    assert body["human_review"]["reasons"]
    if body["resolution"]["status"] == "draft":
        assert body["grounding"]["grounded"] is True and body["sources"]
