"""Retrieval against real Postgres + pgvector with a seeded corpus of known vectors."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from app.core.config import Settings
from app.db.models import Document, DocumentChunk
from app.db.session import create_session_factory
from app.main import create_app
from app.schemas.retrieval import RetrievalMethod
from app.services.retrieval.query import transform_query
from app.services.retrieval.service import RetrievalService
from tests.integration.conftest import TEST_EMBEDDING_DIMENSION as DIM

pytestmark = pytest.mark.integration


def unit(*components: float) -> list[float]:
    v = list(components) + [0.0] * (DIM - len(components))
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


E = [unit(*([0.0] * i + [1.0])) for i in range(DIM)]  # orthonormal basis


class MappingEmbedder:
    """Maps known query strings to fixed vectors; anything else is orthogonal to the corpus."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = mapping
        self.queries: list[str] = []

    spec = f"fake-embedding@{DIM}"
    dimension = DIM
    max_input_tokens = 1800
    requests_made = 0

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        for key, vector in self.mapping.items():
            if key in text:
                return vector
        return E[7]

    async def embed_documents(self, items: Sequence[Any]) -> list[list[float]]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


CORPUS: list[dict[str, Any]] = [
    {
        "document_id": "runner-register", "status": "active", "enabled": True,
        "category": "runners",
        "chunks": [
            ("Register a runner with gitlab-runner register and an authentication token.", E[0],
             "registering-a-runner"),
        ],
    },
    {
        "document_id": "runner-tls", "status": "active", "enabled": True, "category": "runners",
        "chunks": [
            ("If jobs fail with x509: certificate signed by unknown authority, set tls-ca-file"
             " in config.toml.", E[1], "self-signed-certificates"),
        ],
    },
    {
        "document_id": "ci-variables", "status": "active", "enabled": True, "category": "ci_cd",
        "chunks": [
            ("CI/CD variables are environment variables you define for jobs.", E[2], None),
            ("Masked variables hide their values in job logs.", unit(0, 0, 1, 1), None),
        ],
    },
    {
        "document_id": "webhooks", "status": "active", "enabled": True, "category": "webhooks",
        "chunks": [
            (f"Webhook delivery detail number {i}.", unit(0, 0, 0, 0, 1, 0.1 * i), None)
            for i in range(5)
        ],
    },
    # Must never be returned:
    {
        "document_id": "old-register", "status": "superseded", "enabled": True,
        "category": "runners",
        "chunks": [("Register a runner (outdated instructions).", E[0], None)],
    },
    {
        "document_id": "hidden-register", "status": "active", "enabled": False,
        "category": "runners",
        "chunks": [("Register a runner (disabled document).", E[0], None)],
    },
]  # fmt: skip


async def seed(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    async with sessions() as session:
        for spec in CORPUS:
            doc = Document(
                id=uuid.uuid4(), document_id=spec["document_id"], version=1,
                title=spec["document_id"].replace("-", " ").title(),
                canonical_url=f"https://docs.gitlab.com/{spec['document_id']}/",
                category=spec["category"], source_type="product_documentation",
                license="CC BY-SA 4.0", content_hash="h", retrieved_at=datetime.now(UTC),
                status=spec["status"], enabled=spec["enabled"],
            )  # fmt: skip
            session.add(doc)
            for index, (content, vector, anchor) in enumerate(spec["chunks"]):
                session.add(
                    DocumentChunk(
                        chunk_id=f"{spec['document_id']}-{index}", document_pk=doc.id,
                        document_id=doc.document_id, document_version=1, chunk_index=index,
                        title=doc.title, heading_path=doc.title, content=content,
                        content_hash=f"{spec['document_id']}-{index}", token_count=10,
                        category=doc.category, canonical_url=doc.canonical_url, anchor=anchor,
                        has_code=False, is_split_oversize=False,
                        embedding_model=f"fake-embedding@{DIM}", embedding=vector,
                    )
                )  # fmt: skip
        await session.commit()


QUERY_VECTORS = {"register a runner": E[0], "masked variable": unit(0, 0, 1, 1)}


@pytest.fixture
async def corpus(
    alembic_config: Config, make_settings: Callable[..., Settings]
) -> AsyncIterator[tuple[Settings, AsyncEngine]]:
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    url = str(alembic_config.attributes["database_url"])
    settings = make_settings(
        database_url=url,
        embedding_dimension=DIM,
        retrieval_relevance_threshold=0.6,
        retrieval_top_k=5,
        retrieval_candidate_k=20,
        retrieval_max_chunks_per_document=2,
    )
    engine = create_async_engine(url)
    await seed(engine)
    try:
        yield settings, engine
    finally:
        await engine.dispose()


def _service(settings: Settings, engine: AsyncEngine, embedder: Any = None) -> RetrievalService:
    return RetrievalService(
        settings=settings,
        session_factory=create_session_factory(engine),
        embedder=embedder if embedder is not None else MappingEmbedder(QUERY_VECTORS),
    )


async def _search(
    corpus: tuple[Settings, AsyncEngine], text: str, method: RetrievalMethod, **kw: Any
):  # type: ignore[no-untyped-def]
    settings, engine = corpus
    return await _service(settings, engine, kw.pop("embedder", None)).retrieve(
        transform_query(text, max_chars=4000), method=method, **kw
    )


async def test_vector_search_ranks_by_cosine_and_excludes_inactive(corpus: Any) -> None:
    result = await _search(corpus, "how do I register a runner", RetrievalMethod.VECTOR)
    top = result.results[0]
    assert top.document_id == "runner-register"
    assert top.scores.semantic == pytest.approx(1.0) and top.passes_threshold
    assert top.url == "https://docs.gitlab.com/runner-register/#registering-a-runner"
    assert top.source_id == "SRC-001"
    returned = {r.document_id for r in result.results}
    assert not returned & {"old-register", "hidden-register"}
    assert result.evidence_sufficient and result.threshold_basis == "semantic_similarity"
    assert result.diagnostics.candidates["lexical"] == 0


async def test_lexical_search_matches_terms(corpus: Any) -> None:
    result = await _search(corpus, "masked variables in logs", RetrievalMethod.LEXICAL)
    assert result.results[0].chunk_id == "ci-variables-1"
    assert result.results[0].scores.lexical > 0
    assert result.diagnostics.candidates["semantic"] == 0


async def test_exact_error_string_is_found_even_when_semantically_distant(corpus: Any) -> None:
    text = 'Job fails: "x509: certificate signed by unknown authority"'
    vector_only = await _search(corpus, text, RetrievalMethod.VECTOR)
    assert not vector_only.evidence_sufficient  # query vector is orthogonal to the corpus

    hybrid = await _search(corpus, text, RetrievalMethod.HYBRID)
    top = hybrid.results[0]
    assert top.chunk_id == "runner-tls-0"
    assert top.exact_phrase_match and top.passes_threshold
    assert top.scores.semantic < 0.6  # passes on the exact match, not on similarity
    assert top.ranks.phrase == 1
    assert hybrid.evidence_sufficient


async def test_hybrid_fuses_both_lists(corpus: Any) -> None:
    result = await _search(corpus, "masked variable values", RetrievalMethod.HYBRID)
    top = result.results[0]
    assert top.chunk_id == "ci-variables-1"
    assert top.ranks.semantic == 1 and top.ranks.lexical is not None
    expected = 1 / (60 + top.ranks.semantic) + 1 / (60 + top.ranks.lexical)
    assert top.scores.fused == pytest.approx(expected, abs=1e-6)


async def test_weak_query_reports_insufficient_evidence(corpus: Any) -> None:
    result = await _search(corpus, "banana smoothie recipe", RetrievalMethod.HYBRID)
    assert not result.evidence_sufficient
    assert all(not r.passes_threshold for r in result.results)
    assert "below the threshold" in result.reason or "No matching" in result.reason


async def test_per_document_cap_and_dedupe(corpus: Any) -> None:
    embedder = MappingEmbedder({"webhook": unit(0, 0, 0, 0, 1)})
    result = await _search(
        corpus, "webhook delivery detail", RetrievalMethod.HYBRID, embedder=embedder
    )
    webhook_hits = [r for r in result.results if r.document_id == "webhooks"]
    assert len(webhook_hits) == 2  # retrieval_max_chunks_per_document
    assert [r.source_id for r in result.results] == [
        f"SRC-{i:03d}" for i in range(1, len(result.results) + 1)
    ]


async def test_category_filter(corpus: Any) -> None:
    result = await _search(
        corpus, "register a runner", RetrievalMethod.HYBRID, categories=["ci_cd"]
    )
    assert {r.category for r in result.results} <= {"ci_cd"}


async def test_results_are_deterministic(corpus: Any) -> None:
    a = await _search(corpus, "masked variable values", RetrievalMethod.HYBRID)
    b = await _search(corpus, "masked variable values", RetrievalMethod.HYBRID)
    assert [r.chunk_id for r in a.results] == [r.chunk_id for r in b.results]


# --- API ------------------------------------------------------------------------------


@pytest.fixture
async def api(
    corpus: tuple[Settings, AsyncEngine],
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    settings, _ = corpus
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.embedder = MappingEmbedder(QUERY_VECTORS)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield app, client


async def test_search_endpoint(api: Any) -> None:
    _, client = api
    r = await client.post("/api/v1/search", json={"query": "register a runner", "top_k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "hybrid" and body["top_k"] == 3
    assert body["results"][0]["document_id"] == "runner-register"
    assert set(body["results"][0]["scores"]) == {"semantic", "lexical", "fused", "rerank", "final"}


async def test_ticket_retrieve_endpoint(api: Any) -> None:
    _, client = api
    r = await client.post(
        "/api/v1/tickets/retrieve",
        json={
            "title": "Runner registration",
            "description": (
                "I need to register a runner. Error: x509: certificate signed by unknown authority"
            ),
            "method": "hybrid",
        },
    )
    assert r.status_code == 200
    ids = [x["chunk_id"] for x in r.json()["results"]]
    assert "runner-tls-0" in ids and "runner-register-0" in ids


@pytest.mark.parametrize(
    "payload",
    [{}, {"query": ""}, {"query": "x" * 4001}, {"query": "x", "method": "magic"},
     {"query": "x", "top_k": 0}, {"query": "x", "top_k": 51}, {"query": "x", "extra": 1},
     {"query": "x", "categories": ["billing"]}],
)  # fmt: skip
async def test_search_validation(api: Any, payload: dict[str, Any]) -> None:
    _, client = api
    r = await client.post("/api/v1/search", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


async def test_search_without_embedder(api: Any) -> None:
    app, client = api
    app.state.embedder = None
    lexical = await client.post("/api/v1/search", json={"query": "masked", "method": "lexical"})
    body = lexical.json()
    assert lexical.status_code == 200 and body["degraded"] and not body["evidence_sufficient"]
    hybrid = await client.post("/api/v1/search", json={"query": "masked", "method": "hybrid"})
    assert hybrid.status_code == 200 and hybrid.json()["degraded"]
    vector = await client.post("/api/v1/search", json={"query": "masked", "method": "vector"})
    assert vector.status_code == 502
    assert vector.json()["error"]["code"] == "EMBEDDING_PROVIDER_ERROR"


async def test_embedding_outage_degrades_to_exact_phrase_evidence(api: Any) -> None:
    from app.core.errors import EmbeddingQuotaExhaustedError

    app, client = api

    class Exhausted(MappingEmbedder):
        async def embed_query(self, text: str) -> list[float]:
            raise EmbeddingQuotaExhaustedError()

    app.state.embedder = Exhausted({})
    r = await client.post(
        "/api/v1/search",
        json={"query": 'Jobs fail: "x509: certificate signed by unknown authority"'},
    )
    body = r.json()
    assert r.status_code == 200 and body["degraded"] and body["warnings"]
    assert body["evidence_sufficient"] is True
    top = body["results"][0]
    assert top["chunk_id"] == "runner-tls-0" and top["exact_phrase_match"]
    assert all(not x["passes_threshold"] for x in body["results"] if not x["exact_phrase_match"])


async def test_document_chunks_endpoint(api: Any) -> None:
    _, client = api
    r = await client.get("/api/v1/documents/webhooks/chunks", params={"limit": 2, "offset": 1})
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == 5 and [c["chunk_index"] for c in body["items"]] == [1, 2]
    missing = await client.get("/api/v1/documents/nope/chunks")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    hidden = await client.get("/api/v1/documents/old-register/chunks")
    assert hidden.json()["total"] == 0  # superseded version is not the active one
    explicit = await client.get("/api/v1/documents/old-register/chunks", params={"version": 1})
    assert explicit.json()["total"] == 1
