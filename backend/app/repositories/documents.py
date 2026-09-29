"""Data access for documents and chunks."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentChunk, DocumentStatus


async def get_active_documents(
    session: AsyncSession, document_ids: Iterable[str]
) -> dict[str, Document]:
    ids = list(document_ids)
    if not ids:
        return {}
    rows = await session.scalars(
        select(Document).where(
            Document.document_id.in_(ids), Document.status == DocumentStatus.ACTIVE.value
        )
    )
    return {doc.document_id: doc for doc in rows}


async def next_version(session: AsyncSession, document_id: str) -> int:
    current = await session.scalar(
        select(func.max(Document.version)).where(Document.document_id == document_id)
    )
    return (current or 0) + 1


async def find_reusable_embeddings(
    session: AsyncSession, content_hashes: Sequence[str], embedding_spec: str
) -> dict[str, list[float]]:
    """Existing embeddings for identical embedding inputs produced with the same spec."""
    if not content_hashes:
        return {}
    rows = await session.execute(
        select(DocumentChunk.content_hash, DocumentChunk.embedding)
        .where(
            DocumentChunk.content_hash.in_(list(set(content_hashes))),
            DocumentChunk.embedding_model == embedding_spec,
            DocumentChunk.embedding.is_not(None),
        )
        .distinct(DocumentChunk.content_hash)
    )
    return {h: [float(v) for v in emb] for h, emb in rows if emb is not None}


async def set_enabled(session: AsyncSession, document_id: str, enabled: bool) -> int:
    result = await session.execute(
        update(Document)
        .where(Document.document_id == document_id, Document.enabled.is_not(enabled))
        .values(enabled=enabled)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class StagedValidation:
    document_pk: uuid.UUID
    document_id: str
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


async def validate_staged(
    session: AsyncSession, document_pk: uuid.UUID, dimension: int
) -> StagedValidation:
    doc = await session.get(Document, document_pk)
    if doc is None:
        return StagedValidation(document_pk, "?", ("staged document row is missing",))
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS chunks,"
                " count(*) FILTER (WHERE embedding IS NULL) AS missing,"
                " count(*) FILTER (WHERE embedding IS NOT NULL AND vector_dims(embedding) <> :dim)"
                "   AS wrong_dim,"
                " count(*) FILTER (WHERE length(btrim(content)) = 0) AS empty"
                " FROM document_chunks WHERE document_pk = :pk"
            ),
            {"pk": document_pk, "dim": dimension},
        )
    ).one()
    problems: list[str] = []
    if doc.status != DocumentStatus.STAGED.value:
        problems.append(f"expected status staged, found {doc.status}")
    if not doc.content_hash:
        problems.append("content hash not recorded")
    if row.chunks == 0:
        problems.append("no chunks")
    if row.missing:
        problems.append(f"{row.missing} chunks without embeddings")
    if row.wrong_dim:
        problems.append(f"{row.wrong_dim} embeddings with wrong dimension")
    if row.empty:
        problems.append(f"{row.empty} empty chunks")
    return StagedValidation(document_pk, doc.document_id, tuple(problems))


async def mark_failed(session: AsyncSession, document_pks: Iterable[uuid.UUID]) -> None:
    pks = list(document_pks)
    if pks:
        await session.execute(
            update(Document)
            .where(Document.id.in_(pks), Document.status == DocumentStatus.STAGED.value)
            .values(status=DocumentStatus.FAILED.value)
        )


async def publish(session: AsyncSession, document_pks: Sequence[uuid.UUID]) -> int:
    """Atomically make staged versions active and supersede previous active versions.

    Must run inside the caller's transaction. Supersede first so the
    one-active-version unique index is never violated mid-statement.
    """
    if not document_pks:
        return 0
    staged_ids = select(Document.document_id).where(Document.id.in_(document_pks)).scalar_subquery()
    await session.execute(
        update(Document)
        .where(
            Document.document_id.in_(staged_ids),
            Document.status == DocumentStatus.ACTIVE.value,
        )
        .values(status=DocumentStatus.SUPERSEDED.value)
    )
    result = await session.execute(
        update(Document)
        .where(Document.id.in_(document_pks), Document.status == DocumentStatus.STAGED.value)
        .values(status=DocumentStatus.ACTIVE.value, published_at=datetime.now(UTC))
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def fail_orphaned_staged(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Staged rows left behind by a crashed job must never become visible."""
    await session.execute(
        update(Document)
        .where(Document.ingestion_job_id == job_id, Document.status == DocumentStatus.STAGED.value)
        .values(status=DocumentStatus.FAILED.value)
    )


async def embedding_column_dimension(session: AsyncSession) -> int:
    """Dimension of document_chunks.embedding as created by migrations."""
    value = await session.scalar(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
        )
    )
    return int(value or 0)


@dataclass(frozen=True)
class DocumentFilters:
    search: str | None = None
    category: str | None = None
    status: str | None = DocumentStatus.ACTIVE.value
    enabled: bool | None = None


_CHUNK_COUNTS = (
    select(DocumentChunk.document_pk, func.count().label("chunk_count"))
    .group_by(DocumentChunk.document_pk)
    .subquery()
)
_CHUNK_MODEL = (
    select(DocumentChunk.document_pk, func.max(DocumentChunk.embedding_model).label("model"))
    .group_by(DocumentChunk.document_pk)
    .subquery()
)


async def list_documents(
    session: AsyncSession, filters: DocumentFilters, *, offset: int, limit: int
) -> tuple[int, list[tuple[Document, int]]]:
    """Documents matching the filters, one row per stored version, newest first by title."""
    conditions = []
    if filters.status:
        conditions.append(Document.status == filters.status)
    if filters.category:
        conditions.append(Document.category == filters.category)
    if filters.enabled is not None:
        conditions.append(Document.enabled.is_(filters.enabled))
    if filters.search:
        pattern = f"%{filters.search.replace('%', r'\%').replace('_', r'\_')}%"
        conditions.append(
            Document.title.ilike(pattern, escape="\\")
            | Document.document_id.ilike(pattern, escape="\\")
            | Document.canonical_url.ilike(pattern, escape="\\")
        )
    total = int(
        await session.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0
    )
    rows = await session.execute(
        select(Document, func.coalesce(_CHUNK_COUNTS.c.chunk_count, 0))
        .outerjoin(_CHUNK_COUNTS, _CHUNK_COUNTS.c.document_pk == Document.id)
        .where(*conditions)
        .order_by(Document.category, Document.title, Document.document_id, Document.version.desc())
        .offset(offset)
        .limit(limit)
    )
    return total, [(doc, int(count)) for doc, count in rows]


async def document_versions(
    session: AsyncSession, document_id: str
) -> list[tuple[Document, int, str | None]]:
    rows = await session.execute(
        select(
            Document,
            func.coalesce(_CHUNK_COUNTS.c.chunk_count, 0),
            _CHUNK_MODEL.c.model,
        )
        .outerjoin(_CHUNK_COUNTS, _CHUNK_COUNTS.c.document_pk == Document.id)
        .outerjoin(_CHUNK_MODEL, _CHUNK_MODEL.c.document_pk == Document.id)
        .where(Document.document_id == document_id)
        .order_by(Document.version.desc())
    )
    return [(doc, int(count), model) for doc, count, model in rows]


async def knowledge_base_counts(session: AsyncSession) -> dict[str, int]:
    row = (
        await session.execute(
            text(
                "WITH live AS (SELECT id FROM documents WHERE status = 'active' AND enabled)"
                " SELECT (SELECT count(*) FROM live) AS documents,"
                " count(c.id) AS chunks,"
                " count(c.id) FILTER (WHERE c.embedding IS NOT NULL) AS embedded_chunks"
                " FROM document_chunks c JOIN live ON live.id = c.document_pk"
            )
        )
    ).one()
    return {
        "documents": int(row.documents),
        "chunks": int(row.chunks),
        "embedded_chunks": int(row.embedded_chunks),
    }
