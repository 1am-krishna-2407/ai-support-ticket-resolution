"""Data access for ingestion jobs, plus the single-flight advisory lock."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import IngestionAlreadyRunningError
from app.db.models import Document, DocumentStatus, IngestionJob, IngestionJobStatus

# Arbitrary but fixed key for pg_try_advisory_lock; one ingestion at a time per database.
INGESTION_LOCK_KEY = 7_300_001


@asynccontextmanager
async def ingestion_lock(engine: AsyncEngine) -> AsyncIterator[None]:
    """Hold a session-level advisory lock on a dedicated connection for the job's lifetime.

    If the process dies, Postgres releases the lock when the connection closes.
    """
    async with engine.connect() as conn:
        acquired = await conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": INGESTION_LOCK_KEY}
        )
        await conn.commit()
        if not acquired:
            raise IngestionAlreadyRunningError()
        try:
            yield
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": INGESTION_LOCK_KEY})
            await conn.commit()


ACTIVE_JOB_STATES = (
    IngestionJobStatus.QUEUED.value,
    IngestionJobStatus.RUNNING.value,
    IngestionJobStatus.VALIDATING.value,
    IngestionJobStatus.INDEXING.value,
    IngestionJobStatus.PUBLISHING.value,
)


async def recover_interrupted_jobs(session: AsyncSession) -> list[uuid.UUID]:
    """Mark jobs that died mid-run as failed and fail their staged documents.

    Only call while holding the ingestion lock: then no other job can be running, so any
    job still in an active state was interrupted (process killed, container stopped).
    """
    ids = list(
        await session.scalars(
            select(IngestionJob.id).where(IngestionJob.status.in_(ACTIVE_JOB_STATES))
        )
    )
    if not ids:
        return []
    await session.execute(
        update(IngestionJob)
        .where(IngestionJob.id.in_(ids))
        .values(
            status=IngestionJobStatus.FAILED.value,
            finished_at=datetime.now(UTC),
            error_summary=[{"error": "Interrupted", "message": "Job stopped before completion."}],
        )
    )
    await session.execute(
        update(Document)
        .where(Document.ingestion_job_id.in_(ids), Document.status == DocumentStatus.STAGED.value)
        .values(status=DocumentStatus.FAILED.value)
    )
    await session.commit()
    return ids


async def create_job(
    session: AsyncSession, *, trigger: str, config: dict[str, Any]
) -> IngestionJob:
    job = IngestionJob(
        status=IngestionJobStatus.RUNNING.value,
        trigger=trigger,
        config=config,
        total_documents=0,
        processed_documents=0,
        changed_documents=0,
        failed_documents=0,
        total_chunks=0,
        embedding_calls=0,
        reused_embeddings=0,
        error_summary=[],
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.commit()
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> IngestionJob | None:
    return await session.get(IngestionJob, job_id)


async def latest_job(session: AsyncSession) -> IngestionJob | None:
    job: IngestionJob | None = await session.scalar(
        select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(1)
    )
    return job
