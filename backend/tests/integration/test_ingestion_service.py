from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from app.core.config import Settings
from app.core.errors import IngestionAlreadyRunningError
from app.core.tokenizer import get_tokenizer
from app.db.models import Document, DocumentChunk, IngestionJob
from app.db.session import create_session_factory
from app.repositories import documents as doc_repo
from app.repositories.ingestion_jobs import ingestion_lock
from app.services.ingestion.fetcher import FetchError, FetchErrorKind
from app.services.ingestion.parser import GitLabDocsParser
from app.services.ingestion.raw_store import RawStore
from app.services.ingestion.service import IngestionOptions, IngestionService
from tests.fakes import FakeEmbeddingProvider, FakeFetcher
from tests.integration.conftest import TEST_EMBEDDING_DIMENSION

pytestmark = pytest.mark.integration

HTML_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"
RUNNERS_URL = "https://docs.gitlab.com/ci/runners/"
VARIABLES_URL = "https://docs.gitlab.com/ci/variables/"
AUTH_URL = "https://docs.gitlab.com/api/rest/authentication/"


def _entry(document_id: str, url: str, category: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "title": document_id,
        "canonical_url": url,
        "category": category,
        "source_type": "product_documentation",
        "license": "CC BY-SA 4.0",
        "source_version": None,
        "enabled": enabled,
    }


class World:
    def __init__(self, tmp_path: Path, settings: Settings, engine: AsyncEngine) -> None:
        self.tmp_path = tmp_path
        self.settings = settings
        self.engine = engine
        self.sessions = create_session_factory(engine)
        self.manifest = tmp_path / "sources.json"
        self.entries = [
            _entry("ci-runners", RUNNERS_URL, "runners"),
            _entry("ci-variables", VARIABLES_URL, "ci_cd"),
        ]
        self.write_manifest()
        self.fetcher = FakeFetcher(
            {
                RUNNERS_URL: (HTML_DIR / "ci_runners.html").read_text(encoding="utf-8"),
                VARIABLES_URL: (HTML_DIR / "ci_variables.html").read_text(encoding="utf-8"),
                AUTH_URL: (HTML_DIR / "api_rest_authentication.html").read_text(encoding="utf-8"),
            }
        )
        self.embedder = FakeEmbeddingProvider(dimension=TEST_EMBEDDING_DIMENSION)

    def write_manifest(self) -> None:
        self.manifest.write_text(json.dumps(self.entries), encoding="utf-8")

    def service(self) -> IngestionService:
        return IngestionService(
            settings=self.settings,
            engine=self.engine,
            session_factory=self.sessions,
            fetcher=self.fetcher,
            parser=GitLabDocsParser(),
            embedder=self.embedder,
            count_tokens=get_tokenizer(self.settings.tokenizer_encoding).count,
            raw_store=RawStore(self.tmp_path / "raw"),
            manifest_path=self.manifest,
        )

    async def run(self, **options: Any):  # type: ignore[no-untyped-def]
        return await self.service().run(IngestionOptions(**options))

    async def documents(self) -> list[tuple[str, int, str, bool]]:
        async with self.sessions() as s:
            rows = await s.execute(
                select(
                    Document.document_id, Document.version, Document.status, Document.enabled
                ).order_by(Document.document_id, Document.version)
            )
            return [tuple(r) for r in rows]  # type: ignore[misc]

    async def count_chunks(self, **filters: Any) -> int:
        async with self.sessions() as s:
            q = select(func.count()).select_from(DocumentChunk)
            for key, value in filters.items():
                q = q.where(getattr(DocumentChunk, key) == value)
            return int(await s.scalar(q) or 0)


@pytest.fixture
async def world(
    tmp_path: Path,
    alembic_config: Config,
    make_settings: Callable[..., Settings],
) -> AsyncIterator[World]:
    # Alembic's env.py runs its own event loop, so migrate from a worker thread.
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    url = str(alembic_config.attributes["database_url"])
    settings = make_settings(
        database_url=url,
        embedding_dimension=TEST_EMBEDDING_DIMENSION,
        allowed_source_domains=["docs.gitlab.com"],
    )
    engine = create_async_engine(url)
    try:
        yield World(tmp_path, settings, engine)
    finally:
        await engine.dispose()


async def test_first_run_publishes_documents_with_embeddings(world: World) -> None:
    summary = await world.run()
    assert summary.status == "completed", summary.errors
    assert (summary.changed_documents, summary.failed_documents, summary.published_documents) == (
        2,
        0,
        2,
    )
    assert await world.documents() == [
        ("ci-runners", 1, "active", True),
        ("ci-variables", 1, "active", True),
    ]
    total = await world.count_chunks()
    assert total == summary.total_chunks > 0
    assert summary.embedding_calls == world.embedder.requests_made > 0

    async with world.sessions() as s:
        doc = await s.scalar(select(Document).where(Document.document_id == "ci-runners"))
        assert doc is not None
        assert (
            doc.title == "Runners" and doc.license == "CC BY-SA 4.0" and doc.category == "runners"
        )
        assert doc.content_hash and doc.pipeline_signature and doc.published_at
        assert doc.raw_path
        assert await asyncio.to_thread(Path(doc.raw_path).exists)
        chunk = await s.scalar(
            select(DocumentChunk).where(DocumentChunk.document_id == "ci-runners")
        )
        assert chunk is not None and chunk.embedding is not None
        assert len(chunk.embedding) == TEST_EMBEDDING_DIMENSION
        assert chunk.embedding_model == world.embedder.spec
        assert chunk.heading_path.startswith("Runners")
        job = await s.scalar(select(IngestionJob))
        assert job is not None and job.status == "completed" and job.finished_at is not None
        assert (job.total_documents, job.processed_documents, job.changed_documents) == (2, 2, 2)


async def test_second_run_skips_unchanged_documents_without_embedding_calls(world: World) -> None:
    await world.run()
    calls_before = world.embedder.requests_made
    chunks_before = await world.count_chunks()

    summary = await world.run()

    assert summary.status == "completed"
    assert (summary.changed_documents, summary.unchanged_documents) == (0, 2)
    assert summary.embedding_calls == 0 and world.embedder.requests_made == calls_before
    assert await world.count_chunks() == chunks_before
    assert [v for _, v, _, _ in await world.documents()] == [1, 1]


async def test_changed_document_creates_new_version_and_reuses_embeddings(world: World) -> None:
    await world.run()
    original = world.fetcher.pages[RUNNERS_URL]
    assert isinstance(original, str)
    world.fetcher.pages[RUNNERS_URL] = original.replace(
        "<h2 id=runner-execution-flow",
        "<p>A newly documented runner behaviour.</p><h2 id=runner-execution-flow",
        1,
    )
    world.embedder.embedded_texts.clear()

    summary = await world.run()

    assert summary.status == "completed"
    assert (summary.changed_documents, summary.unchanged_documents) == (1, 1)
    assert await world.documents() == [
        ("ci-runners", 1, "superseded", True),
        ("ci-runners", 2, "active", True),
        ("ci-variables", 1, "active", True),
    ]
    # Only the edited chunk is new text; every other chunk reuses its stored embedding.
    assert len(world.embedder.embedded_texts) == 1
    assert "newly documented runner" in world.embedder.embedded_texts[0]
    assert summary.reused_embeddings == summary.total_chunks - 1


async def test_force_reprocesses_but_reuses_all_embeddings(world: World) -> None:
    await world.run()
    calls_before = world.embedder.requests_made
    summary = await world.run(force=True)
    assert summary.changed_documents == 2
    assert world.embedder.requests_made == calls_before
    assert summary.reused_embeddings == summary.total_chunks
    assert [(d, v, st) for d, v, st, _ in await world.documents()] == [
        ("ci-runners", 1, "superseded"),
        ("ci-runners", 2, "active"),
        ("ci-variables", 1, "superseded"),
        ("ci-variables", 2, "active"),
    ]


async def test_pipeline_change_triggers_reprocessing(world: World) -> None:
    await world.run()
    world.settings = world.settings.model_copy(update={"chunk_target_tokens": 200})
    summary = await world.run()
    assert summary.changed_documents == 2


async def test_failed_document_does_not_block_others(world: World) -> None:
    world.entries.append(_entry("api-rest-authentication", AUTH_URL, "authentication"))
    world.write_manifest()
    world.fetcher.pages[AUTH_URL] = FetchError(FetchErrorKind.TIMEOUT, "timed out")

    summary = await world.run()

    assert summary.status == "completed"
    assert (summary.changed_documents, summary.failed_documents, summary.published_documents) == (
        2,
        1,
        2,
    )
    assert summary.errors == [
        {
            "error": "FetchError",
            "message": "timed out",
            "document_id": "api-rest-authentication",
            "kind": "timeout",
        }
    ]


async def test_embedding_failure_leaves_active_version_untouched(world: World) -> None:
    await world.run()
    original = world.fetcher.pages[RUNNERS_URL]
    assert isinstance(original, str)
    world.fetcher.pages[RUNNERS_URL] = original.replace("Runners are", "BROKEN Runners are", 1)
    world.embedder.fail_when_text_contains = "BROKEN"

    summary = await world.run()

    assert summary.failed_documents == 1
    assert await world.documents() == [
        ("ci-runners", 1, "active", True),
        ("ci-variables", 1, "active", True),
    ]


async def test_crash_before_publish_keeps_old_kb_and_fails_staged(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await world.run()
    original = world.fetcher.pages[RUNNERS_URL]
    assert isinstance(original, str)
    world.fetcher.pages[RUNNERS_URL] = original.replace("Runners are", "Updated: Runners are", 1)

    async def exploding_publish(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("database went away")

    monkeypatch.setattr(doc_repo, "publish", exploding_publish)
    summary = await world.run()

    assert summary.status == "failed"
    assert await world.documents() == [
        ("ci-runners", 1, "active", True),
        ("ci-runners", 2, "failed", True),
        ("ci-variables", 1, "active", True),
    ]
    async with world.sessions() as s:
        job = await s.scalar(select(IngestionJob).order_by(IngestionJob.created_at.desc()))
        assert job is not None and job.status == "failed"
        assert job.error_summary[-1]["error"] == "RuntimeError"


async def test_concurrent_run_is_rejected(world: World) -> None:
    async with ingestion_lock(world.engine):
        with pytest.raises(IngestionAlreadyRunningError):
            await world.run()
    # Lock released: a run succeeds afterwards.
    assert (await world.run()).status == "completed"


async def test_disabled_entry_is_skipped_and_hidden(world: World) -> None:
    await world.run()
    world.entries[0]["enabled"] = False
    world.write_manifest()
    world.fetcher.calls.clear()

    summary = await world.run()

    assert summary.total_documents == 1
    assert RUNNERS_URL not in world.fetcher.calls
    assert ("ci-runners", 1, "active", False) in await world.documents()


async def test_only_filter_and_unknown_ids(world: World) -> None:
    summary = await world.run(only=("ci-variables",))
    assert summary.total_documents == 1
    assert [d for d, *_ in await world.documents()] == ["ci-variables"]

    failed = await world.run(only=("does-not-exist",))
    assert failed.status == "failed"
    assert "unknown document_id" in failed.errors[0]["message"]


async def test_quota_exhaustion_defers_remaining_and_publishes_staged(world: World) -> None:
    from app.core.errors import EmbeddingQuotaExhaustedError

    world.entries.append(_entry("api-rest-authentication", AUTH_URL, "authentication"))
    world.write_manifest()
    # The second document's first chunk triggers the daily cap.
    world.embedder.fail_when_text_contains = "CI/CD variables"
    world.embedder.failure = EmbeddingQuotaExhaustedError()

    summary = await world.run()

    assert summary.status == "completed"
    assert (summary.published_documents, summary.deferred_documents) == (1, 2)
    assert AUTH_URL in world.fetcher.calls  # still checked in reuse-only mode
    assert await world.documents() == [("ci-runners", 1, "active", True)]

    world.embedder.fail_when_text_contains = None
    follow_up = await world.run()  # next day: the rest is ingested, the first is unchanged
    assert (follow_up.unchanged_documents, follow_up.changed_documents) == (1, 2)


async def test_quota_exhausted_before_anything_published_is_a_failed_run(world: World) -> None:
    from app.core.errors import EmbeddingQuotaExhaustedError

    world.embedder.fail_when_text_contains = "Runners"
    world.embedder.failure = EmbeddingQuotaExhaustedError()
    summary = await world.run()
    assert summary.status == "failed" and summary.deferred_documents == 2


async def test_interrupted_job_is_recovered_and_its_embeddings_reused(world: World) -> None:
    from app.db.models import IngestionJobStatus
    from app.repositories import ingestion_jobs as jobs

    # Simulate a crash: stage everything, then pretend the process died before publishing.
    async def no_publish(*args: Any, **kwargs: Any) -> int:
        raise KeyboardInterrupt  # not caught by the job's error handling, like a SIGKILL

    import app.repositories.documents as documents_module

    original_publish = documents_module.publish
    documents_module.publish = no_publish  # type: ignore[assignment]
    try:
        with pytest.raises(KeyboardInterrupt):
            await world.run()
    finally:
        documents_module.publish = original_publish  # type: ignore[assignment]
    async with world.sessions() as s:
        job = await s.scalar(select(IngestionJob))
        assert job is not None and job.status == IngestionJobStatus.PUBLISHING.value
    assert {st for _, _, st, _ in await world.documents()} == {"staged"}
    calls_before = world.embedder.requests_made

    summary = await world.run()

    assert summary.status == "completed" and summary.published_documents == 2
    assert world.embedder.requests_made == calls_before  # embeddings reused from staged rows
    async with world.sessions() as s:
        statuses = sorted(j.status for j in await s.scalars(select(IngestionJob)))
        assert statuses == ["completed", "failed"]
        recovered = await s.scalar(select(IngestionJob).where(IngestionJob.status == "failed"))
        assert recovered is not None and recovered.error_summary[0]["error"] == "Interrupted"
    docs = await world.documents()
    assert ("ci-runners", 1, "failed", True) in docs and ("ci-runners", 2, "active", True) in docs
    assert jobs.ACTIVE_JOB_STATES  # sanity: states tuple is exported


async def test_reuse_only_mode_publishes_documents_with_stored_embeddings(world: World) -> None:
    from app.core.errors import EmbeddingQuotaExhaustedError

    # Day 1: everything embedded, then the process dies before publishing (embeddings stored).
    await world.run(only=("ci-variables",))
    # Day 2: a new page needs embeddings but the quota is gone; ci-variables must still publish.
    world.entries.insert(0, _entry("api-rest-authentication", AUTH_URL, "authentication"))
    world.write_manifest()
    world.embedder.fail_when_text_contains = "REST API authentication"
    world.embedder.failure = EmbeddingQuotaExhaustedError()
    calls_before = world.embedder.requests_made

    summary = await world.run(force=True)

    assert summary.deferred_documents == 2  # the new page and ci-runners (never embedded)
    assert summary.published_documents == 1  # ci-variables, entirely from stored embeddings
    assert world.embedder.requests_made == calls_before + 1  # only the call that hit the quota


async def test_dimension_mismatch_is_reported_before_any_work(world: World) -> None:
    from app.core.errors import ServiceUnavailableError

    world.embedder = FakeEmbeddingProvider(dimension=TEST_EMBEDDING_DIMENSION + 1)
    with pytest.raises(ServiceUnavailableError, match="alembic upgrade head"):
        await world.run()
    assert world.fetcher.calls == []
