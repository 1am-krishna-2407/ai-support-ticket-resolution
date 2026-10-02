"""End to end against the running Compose stack, through the frontend's nginx proxy.

    E2E_BASE_URL=http://127.0.0.1:8080 E2E_EMAIL=... E2E_PASSWORD=... pytest tests/e2e
    add E2E_LLM=1 to also analyse a ticket (and save it to that user's history)

E2E_EMAIL / E2E_PASSWORD are an existing agent account on the stack (create one with
`python -m app.scripts.create_user`). They may also come from a git-ignored `.env.e2e`
file at the repository root; E2E_BASE_URL must always be set explicitly.

Covers the real path: nginx (static SPA + /api proxy) → FastAPI → Postgres/pgvector →
the configured embedding provider (one query embedding per search). With E2E_LLM=1 the
analysis test also calls the configured LLM (two to three calls). Skipped when
E2E_BASE_URL is unset. Nothing here needs or sends the admin token.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest


def _from_env_file(name: str) -> str:
    env_file = Path(__file__).resolve().parents[3] / ".env.e2e"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip()
    return ""


# Only an explicit E2E_BASE_URL runs this suite (it spends embedding calls on the live
# stack); .env.e2e supplies just the test account.
BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("E2E_EMAIL") or _from_env_file("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD") or _from_env_file("E2E_PASSWORD")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not set (needs the Compose stack)"),
]
API = "/api/v1"


@pytest.fixture(scope="module")
def anonymous() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL, timeout=600, trust_env=False) as c:
        yield c


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    """Signed in as the E2E agent (session cookie kept by the client)."""
    if not (EMAIL and PASSWORD):
        pytest.skip("E2E_EMAIL / E2E_PASSWORD are not set")
    with httpx.Client(base_url=BASE_URL, timeout=600, trust_env=False) as c:
        r = c.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 200, r.text[:300]
        yield c
        c.post(f"{API}/auth/logout")


def _ok(response: httpx.Response, status: int = 200) -> dict:  # type: ignore[type-arg]
    assert response.status_code == status, response.text[:500]
    assert response.headers.get("x-request-id")
    return response.json()  # type: ignore[no-any-return]


def test_frontend_serves_the_app_and_spa_routes(anonymous: httpx.Client) -> None:
    for path in ("/", "/login", "/tickets", "/history", "/knowledge-base", "/evaluation"):
        r = anonymous.get(path)
        assert r.status_code == 200 and '<div id="root">' in r.text, path


def test_signed_out_requests_are_rejected(anonymous: httpx.Client) -> None:
    for method, path in [("POST", "/search"), ("GET", "/documents"), ("GET", "/tickets/history")]:
        r = anonymous.request(method, f"{API}{path}", json={"query": "x"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED", path


def test_session_identifies_the_user(client: httpx.Client) -> None:
    me = _ok(client.get(f"{API}/auth/me"))
    assert me["user"]["email"] == EMAIL.strip().lower() and "analyses" in me["stats"]


def test_health_version_and_readiness(anonymous: httpx.Client) -> None:
    assert _ok(anonymous.get(f"{API}/health"))["status"] == "ok"
    assert _ok(anonymous.get(f"{API}/version"))["api_version"]
    ready = _ok(anonymous.get(f"{API}/ready"))
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


def test_agents_cannot_run_admin_operations(client: httpx.Client, anonymous: httpx.Client) -> None:
    assert anonymous.post(f"{API}/ingestion/run", json={}).status_code == 401
    assert client.post(f"{API}/ingestion/run", json={}).status_code == 403  # agent
    assert client.get(f"{API}/users").status_code == 403
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
    # Saved to this user's history, retrievable and deletable.
    saved = _ok(client.get(f"{API}/tickets/history/{body['analysis_id']}"))
    assert saved["result"]["resolution"]["status"] == body["resolution"]["status"]
    assert client.delete(f"{API}/tickets/history/{body['analysis_id']}").status_code == 204
