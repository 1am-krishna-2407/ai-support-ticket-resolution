"""Ingestion orchestration: manifest → fetch → parse → chunk → embed → stage → validate → publish.

Guarantees:
- Single flight: a Postgres advisory lock allows one job per database.
- Retrieval only sees `active` + `enabled` documents. New versions are written
  as `staged` and flipped to `active` in one transaction after the whole job's
  validation, so a failed job leaves the existing knowledge base untouched.
- A document is skipped when both its extracted-content hash and its pipeline
  signature match the active version (re-runs make no embedding calls).
- Embeddings are reused for chunks whose embedding input and embedding spec
  already exist, so only genuinely new text is sent to the provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, EmbeddingQuotaExhaustedError, ServiceUnavailableError
from app.db.models import Document, DocumentChunk, DocumentStatus, IngestionJob, IngestionJobStatus
from app.providers.embeddings import EmbeddingInput, EmbeddingProvider
from app.repositories import documents as doc_repo
from app.repositories import ingestion_jobs as job_repo
from app.services.ingestion.chunker import ChunkDraft, Chunker, ChunkingConfig, make_chunk_id
from app.services.ingestion.fetcher import DocumentFetcher, FetchError
from app.services.ingestion.manifest import ManifestEntry, ManifestError, load_manifest
from app.services.ingestion.parser import DocumentParser, ParseError
from app.services.ingestion.raw_store import RawStore

logger = logging.getLogger("app.ingestion")

MAX_ERRORS_RECORDED = 200


class ChunkingError(ValueError):
    pass


class _NeedsEmbeddingQuota(Exception):
    """Raised in reuse-only mode when a document needs new embeddings."""


@dataclass(frozen=True)
class IngestionOptions:
    force: bool = False
    only: tuple[str, ...] = ()
    fail_fast: bool = False
    trigger: str = "cli"


@dataclass
class IngestionSummary:
    job_id: uuid.UUID
    status: str
    total_documents: int = 0
    processed_documents: int = 0
    changed_documents: int = 0
    unchanged_documents: int = 0
    failed_documents: int = 0
    deferred_documents: int = 0
    published_documents: int = 0
    total_chunks: int = 0
    embedding_calls: int = 0
    reused_embeddings: int = 0
    duration_seconds: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["job_id"] = str(self.job_id)
        return data


def pipeline_signature(
    settings: Settings, parser_version: str, embedding_spec: str, entry: ManifestEntry
) -> str:
    payload = {
        "parser": parser_version,
        "chunk": [
            settings.chunk_target_tokens,
            settings.chunk_overlap_tokens,
            settings.chunk_hard_max_tokens,
        ],
        "tokenizer": settings.tokenizer_encoding,
        "embedding": embedding_spec,
        "manifest": entry.model_dump(mode="json", exclude={"enabled", "title"}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class IngestionService:
    def __init__(
        self,
        *,
        settings: Settings,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        fetcher: DocumentFetcher,
        parser: DocumentParser,
        embedder: EmbeddingProvider,
        count_tokens: Any,
        raw_store: RawStore,
        manifest_path: Path,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._sessions = session_factory
        self._fetcher = fetcher
        self._parser = parser
        self._embedder = embedder
        self._raw_store = raw_store
        self._manifest_path = manifest_path
        self._chunker = Chunker(
            ChunkingConfig(
                target_tokens=settings.chunk_target_tokens,
                overlap_tokens=settings.chunk_overlap_tokens,
                hard_max_tokens=settings.chunk_hard_max_tokens,
            ),
            count_tokens,
        )

    async def run(
        self, options: IngestionOptions, *, job_id: uuid.UUID | None = None
    ) -> IngestionSummary:
        """Run ingestion. `job_id` adopts a job row pre-created (queued) by the API."""
        async with job_repo.ingestion_lock(self._engine):
            return await self._run_locked(options, job_id)

    # --- job lifecycle ------------------------------------------------------------

    async def _run_locked(
        self, options: IngestionOptions, existing_job_id: uuid.UUID | None
    ) -> IngestionSummary:
        started = time.monotonic()
        self._quota_exhausted = False
        async with self._sessions() as session:
            column = await doc_repo.embedding_column_dimension(session)
            if column != self._embedder.dimension:
                raise ServiceUnavailableError(
                    f"The embedding column is vector({column}) but {self._embedder.spec} "
                    f"produces {self._embedder.dimension} dimensions. Run the database "
                    "migrations (alembic upgrade head) after changing EMBEDDING_DIMENSION.",
                    details={"column": column, "provider": self._embedder.dimension},
                )
            recovered = await job_repo.recover_interrupted_jobs(session, exclude=existing_job_id)
            if recovered:
                logger.warning(
                    "recovered interrupted ingestion jobs",
                    extra={"job_ids": [str(j) for j in recovered]},
                )
            config = {
                "force": options.force,
                "only": list(options.only),
                "fail_fast": options.fail_fast,
                "manifest": str(self._manifest_path),
                "embedding_spec": self._embedder.spec,
                "parser_version": self._parser.version,
                "chunk_target_tokens": self._settings.chunk_target_tokens,
                "chunk_overlap_tokens": self._settings.chunk_overlap_tokens,
                "chunk_hard_max_tokens": self._settings.chunk_hard_max_tokens,
            }
            if existing_job_id is not None:
                job_id = await job_repo.start_job(session, existing_job_id, config=config)
            else:
                job = await job_repo.create_job(session, trigger=options.trigger, config=config)
                job_id = job.id
            summary = IngestionSummary(job_id=job_id, status=IngestionJobStatus.RUNNING.value)
            requests_before = self._embedder.requests_made
            staged: list[uuid.UUID] = []
            try:
                entries = self._select_entries(options)
                await self._sync_enabled_flags(session, entries)
                enabled = [e for e in entries if e.enabled]
                summary.total_documents = len(enabled)
                await self._save_progress(session, summary, requests_before)

                for entry in enabled:
                    try:
                        pk = await self._process(session, job_id, entry, options.force, summary)
                    except EmbeddingQuotaExhaustedError as exc:
                        # From here on, only documents whose embeddings can all be reused are
                        # processed; the rest are deferred to a run after the quota resets.
                        await session.rollback()
                        self._quota_exhausted = True
                        summary.deferred_documents += 1
                        self._record_error(summary, entry.document_id, exc)
                        logger.warning(
                            "embedding quota exhausted; continuing in reuse-only mode",
                            extra={"document_id": entry.document_id},
                        )
                        continue
                    except _NeedsEmbeddingQuota:
                        await session.rollback()
                        summary.deferred_documents += 1
                        continue
                    except (FetchError, ParseError, ChunkingError, AppError) as exc:
                        await session.rollback()
                        summary.failed_documents += 1
                        self._record_error(summary, entry.document_id, exc)
                        logger.warning(
                            "document failed",
                            extra={"document_id": entry.document_id, "error": type(exc).__name__},
                        )
                        if options.fail_fast:
                            raise
                    else:
                        if pk is not None:
                            staged.append(pk)
                    summary.processed_documents += 1
                    await self._save_progress(session, summary, requests_before)

                await self._set_status(session, job_id, IngestionJobStatus.VALIDATING)
                valid = await self._validate(session, staged, summary)

                await self._set_status(session, job_id, IngestionJobStatus.INDEXING)
                # HNSW and GIN indexes update on insert; refresh planner statistics.
                await session.execute(text("ANALYZE document_chunks"))

                await self._set_status(session, job_id, IngestionJobStatus.PUBLISHING)
                summary.published_documents = await doc_repo.publish(session, valid)
                await session.commit()

                # Nothing published and nothing left unchanged: the run achieved nothing.
                all_failed = (
                    summary.total_documents > 0
                    and summary.published_documents == 0
                    and summary.unchanged_documents == 0
                    and (summary.failed_documents > 0 or summary.deferred_documents > 0)
                )
                summary.status = (
                    IngestionJobStatus.FAILED.value
                    if all_failed
                    else IngestionJobStatus.COMPLETED.value
                )
            except Exception as exc:
                await session.rollback()
                summary.status = IngestionJobStatus.FAILED.value
                self._record_error(summary, None, exc)
                logger.error("ingestion job failed", extra={"error": type(exc).__name__})
                await doc_repo.fail_orphaned_staged(session, job_id)
                await session.commit()
            finally:
                summary.duration_seconds = round(time.monotonic() - started, 2)
                # Interrupted (cancelled/killed) runs keep their last stage; the next run's
                # recovery marks them failed. Only completed/failed runs are finalised here.
                terminal = summary.status in (
                    IngestionJobStatus.COMPLETED.value,
                    IngestionJobStatus.FAILED.value,
                )
                await self._save_progress(session, summary, requests_before, finished=terminal)
            return summary

    def _select_entries(self, options: IngestionOptions) -> list[ManifestEntry]:
        entries = load_manifest(self._manifest_path, self._settings.allowed_source_domains)
        if not options.only:
            return entries
        wanted = set(options.only)
        unknown = wanted - {e.document_id for e in entries}
        if unknown:
            raise ManifestError([f"unknown document_id: {d}" for d in sorted(unknown)])
        return [e for e in entries if e.document_id in wanted]

    async def _sync_enabled_flags(
        self, session: AsyncSession, entries: list[ManifestEntry]
    ) -> None:
        for entry in entries:
            await doc_repo.set_enabled(session, entry.document_id, entry.enabled)
        await session.commit()

    # Job rows are updated by id with explicit UPDATEs: ORM instances are expired by
    # the per-document rollbacks, and reloading them lazily is not possible in async code.

    async def _set_status(
        self, session: AsyncSession, job_id: uuid.UUID, status: IngestionJobStatus
    ) -> None:
        await session.execute(
            update(IngestionJob).where(IngestionJob.id == job_id).values(status=status.value)
        )
        await session.commit()

    async def _save_progress(
        self,
        session: AsyncSession,
        summary: IngestionSummary,
        requests_before: int,
        *,
        finished: bool = False,
    ) -> None:
        summary.embedding_calls = self._embedder.requests_made - requests_before
        values: dict[str, Any] = {
            "total_documents": summary.total_documents,
            "processed_documents": summary.processed_documents,
            "changed_documents": summary.changed_documents,
            "failed_documents": summary.failed_documents,
            "total_chunks": summary.total_chunks,
            "embedding_calls": summary.embedding_calls,
            "reused_embeddings": summary.reused_embeddings,
            "error_summary": summary.errors[:MAX_ERRORS_RECORDED],
        }
        if finished:
            values["status"] = summary.status
            values["finished_at"] = datetime.now(UTC)
        await session.execute(
            update(IngestionJob).where(IngestionJob.id == summary.job_id).values(**values)
        )
        await session.commit()

    @staticmethod
    def _record_error(
        summary: IngestionSummary, document_id: str | None, exc: BaseException
    ) -> None:
        entry: dict[str, Any] = {"error": type(exc).__name__, "message": str(exc)[:500]}
        if document_id:
            entry["document_id"] = document_id
        kind = getattr(exc, "kind", None) or getattr(exc, "code", None)
        if kind is not None:
            entry["kind"] = str(kind)
        summary.errors.append(entry)

    # --- per document -------------------------------------------------------------

    async def _process(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        entry: ManifestEntry,
        force: bool,
        summary: IngestionSummary,
    ) -> uuid.UUID | None:
        fetched = await self._fetcher.fetch(entry.canonical_url)
        raw_path = self._raw_store.save(entry.document_id, fetched.body)
        html = fetched.body.decode("utf-8", errors="replace")
        parsed = self._parser.parse(html, entry.canonical_url)
        signature = pipeline_signature(
            self._settings, self._parser.version, self._embedder.spec, entry
        )

        active = (await doc_repo.get_active_documents(session, [entry.document_id])).get(
            entry.document_id
        )
        if (
            active is not None
            and not force
            and active.content_hash == parsed.content_hash
            and active.pipeline_signature == signature
        ):
            summary.unchanged_documents += 1
            return None

        drafts = self._chunker.chunk(parsed)
        if not drafts:
            raise ChunkingError(f"{entry.document_id}: no chunks produced")
        # Each provider states a safe input size (Gemini truncates silently above its limit).
        limit = self._embedder.max_input_tokens
        oversize = [d.index for d in drafts if d.token_count > limit]
        if oversize:
            raise ChunkingError(f"{entry.document_id}: chunks {oversize} exceed {limit} tokens")

        embeddings = await self._embed(session, parsed.title, drafts, summary)
        version = await doc_repo.next_version(session, entry.document_id)
        document = Document(
            id=uuid.uuid4(),
            document_id=entry.document_id,
            version=version,
            title=parsed.title or entry.title,
            canonical_url=entry.canonical_url,
            category=entry.category.value,
            source_type=entry.source_type,
            license=entry.license,
            source_version=entry.source_version,
            content_hash=parsed.content_hash,
            retrieved_at=fetched.fetched_at,
            status=DocumentStatus.STAGED.value,
            enabled=entry.enabled,
            raw_path=str(raw_path),
            pipeline_signature=signature,
            ingestion_job_id=job_id,
        )
        session.add(document)
        for draft, vector in zip(drafts, embeddings, strict=True):
            session.add(self._chunk_row(document, draft, vector))
        await session.commit()

        summary.changed_documents += 1
        summary.total_chunks += len(drafts)
        logger.info(
            "document staged",
            extra={"document_id": entry.document_id, "version": version, "chunks": len(drafts)},
        )
        return document.id

    async def _embed(
        self,
        session: AsyncSession,
        title: str,
        drafts: list[ChunkDraft],
        summary: IngestionSummary,
    ) -> list[list[float]]:
        hashes = [d.content_hash for d in drafts]
        reusable = await doc_repo.find_reusable_embeddings(session, hashes, self._embedder.spec)
        missing: dict[str, ChunkDraft] = {}
        for draft in drafts:
            if draft.content_hash not in reusable:
                missing.setdefault(draft.content_hash, draft)
        if missing and self._quota_exhausted:
            raise _NeedsEmbeddingQuota
        if missing:
            vectors = await self._embedder.embed_documents(
                [EmbeddingInput(d.embedding_text, title=title) for d in missing.values()]
            )
            reusable.update(zip(missing.keys(), vectors, strict=True))
        summary.reused_embeddings += len(drafts) - len(missing)
        return [reusable[h] for h in hashes]

    def _chunk_row(
        self, document: Document, draft: ChunkDraft, vector: list[float]
    ) -> DocumentChunk:
        content_hash = draft.content_hash
        return DocumentChunk(
            chunk_id=make_chunk_id(
                document.document_id, document.version, draft.index, content_hash
            ),
            document_pk=document.id,
            document_id=document.document_id,
            document_version=document.version,
            chunk_index=draft.index,
            title=document.title,
            section=draft.section,
            subsection=draft.subsection,
            heading_path=" > ".join(draft.heading_path),
            content=draft.content,
            content_hash=content_hash,
            token_count=draft.token_count,
            category=document.category,
            canonical_url=document.canonical_url,
            anchor=draft.anchor,
            has_code=draft.has_code,
            is_split_oversize=draft.is_split_oversize,
            embedding_model=self._embedder.spec,
            embedding=vector,
            extra_metadata={"heading_path": list(draft.heading_path)},
        )

    async def _validate(
        self, session: AsyncSession, staged: list[uuid.UUID], summary: IngestionSummary
    ) -> list[uuid.UUID]:
        valid: list[uuid.UUID] = []
        invalid: list[uuid.UUID] = []
        for pk in staged:
            result = await doc_repo.validate_staged(session, pk, self._embedder.dimension)
            if result.ok:
                valid.append(pk)
            else:
                invalid.append(pk)
                summary.failed_documents += 1
                summary.errors.append(
                    {
                        "document_id": result.document_id,
                        "error": "ValidationFailed",
                        "message": "; ".join(result.problems),
                    }
                )
        await doc_repo.mark_failed(session, invalid)
        await session.commit()
        return valid
