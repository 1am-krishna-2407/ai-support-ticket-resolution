"""End to end: HTTP → triage → real pgvector retrieval → generation → validation → response.

Providers are scripted fakes; the database, retrieval SQL, validators, and API are real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from tests.auth_helpers import login_as
from tests.fakes import FakeLLMProvider
from tests.integration.test_retrieval import QUERY_VECTORS, MappingEmbedder, corpus  # noqa: F401
from tests.unit.test_ticket_analysis import triage_json

pytestmark = pytest.mark.integration

TICKET = {
    "title": "How do I register a runner?",
    "description": "We need to register a runner for our project and are not sure which command to use.",
    "environment": {"deployment": "self-managed", "gitlab_version": "17.4"},
}
RESOLUTION = json.dumps(
    {
        "answer_type": "resolution",
        "summary": "The customer needs to register a runner.",
        "resolution_steps": [
            {"instruction": "Run `gitlab-runner register` with an authentication token.",
             "source_ids": ["SRC-001"]},
        ],
        "explanation": "Registration connects the runner to GitLab.",
        "warnings": [], "questions_for_customer": [], "source_ids": ["SRC-001"],
        "confidence": "medium", "grounded": True,
    }
)  # fmt: skip


@pytest.fixture
async def client(
    corpus: tuple[Settings, AsyncEngine],  # noqa: F811
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings, _ = corpus
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.embedder = MappingEmbedder(QUERY_VECTORS)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await login_as(app, c)
            yield app, c


async def test_analyze_end_to_end(client: Any) -> None:
    app, c = client
    app.state.llm = FakeLLMProvider({"triage": [triage_json()], "resolution": [RESOLUTION]})
    r = await c.post("/api/v1/tickets/analyze", json=TICKET)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"].startswith("AI-generated draft")
    assert body["resolution"]["status"] == "draft"
    assert body["grounding"]["grounded"] is True
    [source] = body["sources"]
    assert source["source_id"] == "SRC-001"
    assert source["url"] == "https://docs.gitlab.com/runner-register/#registering-a-runner"
    assert body["retrieval"]["evidence_sufficient"] is True
    assert body["model"]["llm"] == "fake-llm" and body["model"]["embedding"] == "fake-embedding@8"
    assert r.headers["x-request-id"]


async def test_analyze_unsupported_ticket_gets_safe_response(client: Any) -> None:
    app, c = client
    llm = FakeLLMProvider({"triage": [triage_json(category="configuration")]})
    app.state.llm = llm
    r = await c.post(
        "/api/v1/tickets/analyze",
        json={
            "title": "Banana smoothie",
            "description": "How do I make a banana smoothie at home?",
        },
    )
    body = r.json()
    assert r.status_code == 200
    assert body["resolution"]["status"] == "insufficient_evidence"
    assert body["resolution"]["steps"] == []
    assert "insufficient_evidence" in body["human_review"]["reasons"]
    assert llm.calls_for("resolution") == 0


async def test_analyze_without_llm_configured(client: Any) -> None:
    app, c = client
    app.state.llm = None
    r = await c.post("/api/v1/tickets/analyze", json=TICKET)
    body = r.json()
    assert r.status_code == 200
    assert body["resolution"]["status"] == "unavailable"
    assert {"classification_unavailable", "llm_unavailable"} <= set(body["human_review"]["reasons"])


async def test_classify_endpoint(client: Any) -> None:
    app, c = client
    app.state.llm = FakeLLMProvider({"triage": [triage_json()]})
    r = await c.post("/api/v1/tickets/classify", json=TICKET)
    assert r.status_code == 200
    assert r.json()["classification"]["category"] == "runners"
    assert r.json()["priority"]["is_estimate"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "ab", "description": "long enough description"},
        {"title": "Valid title", "description": "short"},
        {"title": "x" * 301, "description": "long enough description"},
        {"title": "Valid title", "description": "x" * 20_001},
        {"title": "Valid title", "description": "long enough description", "priority": "high"},
        {
            "title": "Valid title",
            "description": "long enough",
            "environment": {"deployment": "mars"},
        },
    ],
)
async def test_ticket_validation(client: Any, payload: dict[str, Any]) -> None:
    _, c = client
    for path in ("/api/v1/tickets/analyze", "/api/v1/tickets/classify"):
        r = await c.post(path, json=payload)
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"


async def test_request_logs_hold_metadata_but_never_ticket_text(client: Any) -> None:
    from sqlalchemy import select

    from app.db.models import RequestLog

    app, c = client
    app.state.llm = FakeLLMProvider({"triage": [triage_json()], "resolution": [RESOLUTION]})
    r = await c.post("/api/v1/tickets/analyze", json=TICKET)
    assert r.status_code == 200
    bad = await c.post("/api/v1/tickets/analyze", json={"title": ""})
    assert bad.status_code == 422
    await c.get("/api/v1/health")  # skipped: polled by health checks
    await app.state.request_log_writer.flush()

    async with app.state.session_factory() as session:
        rows = (
            (await session.execute(select(RequestLog).order_by(RequestLog.created_at)))
            .scalars()
            .all()
        )
    assert [(row.path, row.status_code) for row in rows] == [
        ("/api/v1/auth/login", 200),  # the fixture's sign-in
        ("/api/v1/tickets/analyze", 200),
        ("/api/v1/tickets/analyze", 422),
    ]
    login_row, ok, invalid = rows
    assert login_row.user_id is None and ok.user_id is not None  # analyses are attributed
    assert ok.request_id == r.headers["x-request-id"] and ok.route_class == "tickets"
    assert ok.outcome and ok.outcome["status"] == "draft" and ok.outcome["grounded"] is True
    assert ok.stage_timings and "total" in ok.stage_timings
    assert invalid.error_code == "INVALID_REQUEST"
    stored = json.dumps(
        [[row.path, row.outcome, row.stage_timings, row.error_code] for row in rows]
    )
    for text in (TICKET["title"], TICKET["description"], "gitlab-runner register"):
        assert text not in stored


async def test_analyses_are_saved_per_user_and_isolated(client: Any) -> None:
    from tests.auth_helpers import login_as

    app, c = client  # signed in as agent@example.test
    app.state.llm = FakeLLMProvider({"triage": [triage_json()], "resolution": [RESOLUTION]})
    r = await c.post("/api/v1/tickets/analyze", json=TICKET)
    analysis_id = r.json()["analysis_id"]
    assert analysis_id

    mine = (await c.get("/api/v1/tickets/history")).json()
    assert mine["total"] == 1 and mine["items"][0]["title"] == TICKET["title"]
    assert mine["items"][0]["status"] == "draft"
    detail = (await c.get(f"/api/v1/tickets/history/{analysis_id}")).json()
    assert detail["description"] == TICKET["description"]
    assert detail["result"]["resolution"]["status"] == "draft"
    assert detail["result"]["analysis_id"] == analysis_id
    stats = (await c.get("/api/v1/auth/me")).json()["stats"]
    assert stats["analyses"] == 1 and stats["drafts"] == 1 and stats["last_analysis_at"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as other:
        await login_as(app, other, role="agent", email="other@example.test")
        assert (await other.get("/api/v1/tickets/history")).json()["total"] == 0
        # Another user's analysis is "not found", not "forbidden".
        assert (await other.get(f"/api/v1/tickets/history/{analysis_id}")).status_code == 404
        assert (await other.delete(f"/api/v1/tickets/history/{analysis_id}")).status_code == 404
        assert (await other.get("/api/v1/tickets/history?scope=all")).status_code == 403

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as admin:
        await login_as(app, admin, role="admin", email="lead@example.test")
        everything = (await admin.get("/api/v1/tickets/history?scope=all")).json()
        assert everything["total"] == 1
        assert everything["items"][0]["user_email"] == "agent@example.test"
        assert (await admin.get(f"/api/v1/tickets/history/{analysis_id}")).status_code == 200

    assert (await c.delete(f"/api/v1/tickets/history/{analysis_id}")).status_code == 204
    assert (await c.get("/api/v1/tickets/history")).json()["total"] == 0
    assert (await c.get(f"/api/v1/tickets/history/{analysis_id}")).status_code == 404
