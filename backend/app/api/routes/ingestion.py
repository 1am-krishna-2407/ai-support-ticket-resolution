"""Ingestion control. Triggering is admin-only (ADR D10) and single-flight."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, FastAPI, Request, status

from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.core.errors import (
    AppError,
    EmbeddingProviderError,
    IngestionAlreadyRunningError,
    NotFoundError,
)
from app.core.security import require_admin
from app.db.models import IngestionJob, IngestionJobStatus
from app.repositories import ingestion_jobs as job_repo
from app.schemas.documents import IngestionJobView, IngestionRunRequest, IngestionStatus
from app.services.ingestion.factory import build_ingestion_service
from app.services.ingestion.service import IngestionOptions

router = APIRouter(tags=["ingestion"])
logger = logging.getLogger("app.api.ingestion")


def _view(job: IngestionJob) -> IngestionJobView:
    return IngestionJobView.model_validate(job, from_attributes=True)


def _running(request: Request) -> bool:
    task: asyncio.Task[None] | None = getattr(request.app.state, "ingestion_task", None)
    return task is not None and not task.done()


@router.post(
    "/ingestion/run",
    response_model=IngestionJobView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
    summary="Start an ingestion job in the background (admin only)",
)
async def run_ingestion(
    body: IngestionRunRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> IngestionJobView:
    app = request.app
    embedder = app.state.embedder
    if embedder is None:
        raise EmbeddingProviderError("Embedding credentials are not configured.")
    # In-process guard closes the race between two requests; the Postgres advisory lock
    # taken by the job itself protects against other processes (for example the CLI).
    lock: asyncio.Lock = app.state.ingestion_start_lock
    async with lock:
        if _running(request):
            raise IngestionAlreadyRunningError()
        async with job_repo.ingestion_lock(app.state.engine):
            pass  # probe: raises INGESTION_ALREADY_RUNNING if another process holds the lock
        job = await job_repo.create_job(
            session,
            trigger="api",
            config={"force": body.force, "only": body.only},
            status=IngestionJobStatus.QUEUED,
        )
        job_id = job.id
        options = IngestionOptions(force=body.force, only=tuple(body.only), trigger="api")
        app.state.ingestion_task = asyncio.create_task(
            _run_in_background(app, settings, job_id, options), name=f"ingestion-{job_id}"
        )
    await session.refresh(job)
    return _view(job)


async def _run_in_background(
    app: FastAPI, settings: Settings, job_id: uuid.UUID, options: IngestionOptions
) -> None:
    fetcher = app.state.fetcher_factory(settings)
    try:
        service = build_ingestion_service(
            settings,
            engine=app.state.engine,
            session_factory=app.state.session_factory,
            fetcher=fetcher,
            embedder=app.state.embedder,
        )
        await service.run(options, job_id=job_id)
    except Exception as exc:  # the job must never stay "queued" after a failed start
        logger.error("ingestion job failed to run", extra={"job_id": str(job_id)}, exc_info=exc)
        message = exc.message if isinstance(exc, AppError) else "Unexpected error."
        async with app.state.session_factory() as session:
            await job_repo.fail_job(session, job_id, error=type(exc).__name__, message=message)
    finally:
        await fetcher.aclose()


@router.get("/ingestion/status", response_model=IngestionStatus, summary="Latest ingestion job")
async def ingestion_status(request: Request, session: SessionDep) -> IngestionStatus:
    latest = await job_repo.latest_job(session)
    return IngestionStatus(running=_running(request), latest=_view(latest) if latest else None)


@router.get("/ingestion/{job_id}", response_model=IngestionJobView, summary="One ingestion job")
async def ingestion_job(job_id: uuid.UUID, session: SessionDep) -> IngestionJobView:
    job = await job_repo.get_job(session, job_id)
    if job is None:
        raise NotFoundError("Ingestion job not found.", details={"job_id": str(job_id)})
    return _view(job)
