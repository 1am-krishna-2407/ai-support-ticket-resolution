"""Phase 5 routes: readiness, documents, ingestion control (real DB, fake providers)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from tests.fakes import FakeEmbeddingProvider, FakeFetcher
from tests.integration.conftest import TEST_EMBEDDING_DIMENSION
from tests.integration.test_retrieval import QUERY_VECTORS, MappingEmbedder, corpus  # noqa: F401

pytestmark = pytest.mark.integration

ADMIN = {"X-Admin-Token": "test-admin-token-0123456789"}
HTML_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"
RUNNERS_URL = "https://docs.gitlab.com/ci/runners/"


@pytest.fixture
async def api(
    corpus: tuple[Settings, AsyncEngine],  # noqa: F811
    tmp_path: Path,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings, _ = corpus
    manifest = tmp_path / "sources.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "document_id": "ci-runners", "title": "Runners", "canonical_url": RUNNERS_URL,
                    "category": "runners", "source_type": "product_documentation",
                    "license": "CC BY-SA 4.0", "source_version": None, "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )  # fmt: skip
    settings = settings.model_copy(
        update={"manifest_path": str(manifest), "raw_storage_dir": str(tmp_path / "raw")}
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.embedder = MappingEmbedder(QUERY_VECTORS)
        transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield app, c


# --- readiness ------------------------------------------------------------------------------


async def test_ready_reports_each_dependency(api: Any) -> None:
    _, c = api
    r = await c.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    checks = {x["name"]: x["status"] for x in body["checks"]}
    assert checks == {
        "api": "ok",
        "database": "ok",
        "vector_search": "ok",
        "knowledge_base": "ok",
        "embedding_provider": "ok",
        "llm_provider": "not_configured",
    }
    assert body["status"] == "degraded"  # LLM not configured in tests
    assert body["knowledge_base"]["documents"] == 4  # retrievable: active and enabled


async def test_ready_reports_provider_failures_without_probing(api: Any) -> None:
    app, c = api
    app.state.embedder.last_failure = {"code": "EMBEDDING_PROVIDER_ERROR", "message": "credits"}
    body = (await c.get("/api/v1/ready")).json()
    embed = next(x for x in body["checks"] if x["name"] == "embedding_provider")
    assert embed["status"] == "degraded" and "credits" in embed["detail"]


async def test_ready_is_503_when_database_is_down(api: Any) -> None:
    app, c = api

    class Broken:
        def __call__(self) -> Any:
            raise OSError("connection refused")

    app.state.session_factory = Broken()
    r = await c.get("/api/v1/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


# --- documents ------------------------------------------------------------------------------


async def test_list_documents_filters_and_pagination(api: Any) -> None:
    _, c = api
    body = (await c.get("/api/v1/documents")).json()
    assert body["total"] == 5  # active versions, including the disabled one
    assert body["attribution"].startswith("Content from GitLab Docs")
    assert (await c.get("/api/v1/documents", params={"enabled": True})).json()["total"] == 4
    hits = (await c.get("/api/v1/documents", params={"search": "webhook"})).json()
    assert [d["document_id"] for d in hits["items"]] == ["webhooks"]
    assert hits["items"][0]["chunk_count"] == 5
    runners = (await c.get("/api/v1/documents", params={"category": "runners"})).json()
    assert {d["category"] for d in runners["items"]} == {"runners"}
    everything = (await c.get("/api/v1/documents", params={"all_statuses": True})).json()
    assert everything["total"] == 6  # includes the superseded version
    page = (await c.get("/api/v1/documents", params={"limit": 2, "offset": 2})).json()
    assert len(page["items"]) == 2 and page["offset"] == 2


@pytest.mark.parametrize(
    "params",
    [{"category": "billing"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "x"}],
)
async def test_list_documents_validation(api: Any, params: dict[str, Any]) -> None:
    _, c = api
    r = await c.get("/api/v1/documents", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"


async def test_search_escapes_like_wildcards(api: Any) -> None:
    _, c = api
    body = (await c.get("/api/v1/documents", params={"search": "%"})).json()
    assert body["total"] == 0


async def test_document_detail_and_versions(api: Any) -> None:
    _, c = api
    r = await c.get("/api/v1/documents/runner-register")
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "active" and body["chunk_count"] == 1
    assert body["embedding_model"] == f"fake-embedding@{TEST_EMBEDDING_DIMENSION}"
    assert [v["version"] for v in body["versions"]] == [1]
    missing = await c.get("/api/v1/documents/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


# --- ingestion ------------------------------------------------------------------------------


async def test_ingestion_run_requires_admin_token(api: Any) -> None:
    _, c = api
    r = await c.post("/api/v1/ingestion/run", json={})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    r = await c.post("/api/v1/ingestion/run", json={}, headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 403


class BlockingFetcher(FakeFetcher):
    def __init__(self, pages: dict[str, str | Exception], gate: asyncio.Event) -> None:
        super().__init__(pages)
        self.gate = gate

    async def fetch(self, url: str):  # type: ignore[no-untyped-def]
        await self.gate.wait()
        return await super().fetch(url)


async def _wait_for_finish(c: httpx.AsyncClient) -> dict[str, Any]:
    for _ in range(200):
        status = (await c.get("/api/v1/ingestion/status")).json()
        if not status["running"]:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError("ingestion did not finish")


async def test_ingestion_run_lifecycle_and_single_flight(api: Any) -> None:
    app, c = api
    gate = asyncio.Event()
    html = (HTML_DIR / "ci_runners.html").read_text(encoding="utf-8")
    app.state.fetcher_factory = lambda settings: BlockingFetcher({RUNNERS_URL: html}, gate)
    app.state.embedder = FakeEmbeddingProvider(dimension=TEST_EMBEDDING_DIMENSION)

    first = await c.post("/api/v1/ingestion/run", json={}, headers=ADMIN)
    assert first.status_code == 202
    job = first.json()
    assert job["status"] == "queued" and job["trigger"] == "api"

    second = await c.post("/api/v1/ingestion/run", json={}, headers=ADMIN)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INGESTION_ALREADY_RUNNING"
    assert (await c.get("/api/v1/ingestion/status")).json()["running"] is True

    gate.set()
    status = await _wait_for_finish(c)
    assert status["latest"]["id"] == job["id"]
    assert status["latest"]["status"] == "completed"
    assert status["latest"]["changed_documents"] == 1

    detail = await c.get(f"/api/v1/ingestion/{job['id']}")
    assert detail.status_code == 200 and detail.json()["total_chunks"] > 0
    doc = (await c.get("/api/v1/documents/ci-runners")).json()
    assert doc["status"] == "active" and doc["chunk_count"] > 0


async def test_failed_start_does_not_leave_job_queued(api: Any) -> None:
    app, c = api
    # Wrong dimension: the service refuses to start; the pre-created job must be failed.
    app.state.embedder = FakeEmbeddingProvider(dimension=TEST_EMBEDDING_DIMENSION + 1)
    job = (await c.post("/api/v1/ingestion/run", json={}, headers=ADMIN)).json()
    status = await _wait_for_finish(c)
    assert status["latest"]["id"] == job["id"] and status["latest"]["status"] == "failed"
    assert "alembic upgrade head" in status["latest"]["error_summary"][0]["message"]


async def test_ingestion_job_not_found_and_validation(api: Any) -> None:
    _, c = api
    r = await c.get(f"/api/v1/ingestion/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert (await c.get("/api/v1/ingestion/not-a-uuid")).status_code == 422
    r = await c.post("/api/v1/ingestion/run", json={"force": "yes please"}, headers=ADMIN)
    assert r.status_code == 422


async def test_ingestion_status_before_any_job(api: Any) -> None:
    _, c = api
    body = (await c.get("/api/v1/ingestion/status")).json()
    assert body == {"running": False, "latest": None}
