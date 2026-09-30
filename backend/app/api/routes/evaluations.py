"""Evaluation runs. Starting one is admin-only and single-flight; results are readable."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query, Request, status

from app.api.deps import SessionDep, SettingsDep
from app.api.responses import ADMIN, READ, errors
from app.core.errors import (
    AppError,
    EvaluationAlreadyRunningError,
    InvalidRequestError,
    LLMProviderError,
    NotFoundError,
)
from app.core.security import require_admin
from app.db.models import EvaluationRun, EvaluationRunStatus
from app.schemas.evaluation import (
    EvaluationCaseView,
    EvaluationRunDetail,
    EvaluationRunList,
    EvaluationRunRequest,
    EvaluationRunView,
)
from app.services.evaluation.dataset import EvaluationDataset, load_dataset
from app.services.evaluation.factory import build_evaluation_runner
from app.services.evaluation.runner import (
    EvaluationOptions,
    EvaluationRunner,
    get_run,
    list_runs,
)

router = APIRouter(tags=["evaluations"])
logger = logging.getLogger("app.api.evaluations")


def _view(run: EvaluationRun) -> EvaluationRunView:
    return EvaluationRunView.model_validate(run, from_attributes=True)


def _running(app: FastAPI) -> bool:
    task: asyncio.Task[None] | None = getattr(app.state, "evaluation_task", None)
    return task is not None and not task.done()


@router.post(
    "/evaluations/run",
    responses=ADMIN | errors(400, 502),
    response_model=EvaluationRunView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
    summary="Start an evaluation run in the background (admin only)",
)
async def run_evaluation(
    body: EvaluationRunRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> EvaluationRunView:
    app = request.app
    if body.generation and app.state.llm is None:
        raise LLMProviderError("Generation requested but the LLM provider is not configured.")
    dataset = await asyncio.to_thread(load_dataset, Path(settings.evaluation_dataset_path))
    unknown = sorted(set(body.case_types) - {c.type for c in dataset.cases})
    if unknown:
        raise InvalidRequestError("Unknown case types.", details={"case_types": unknown})
    runner = build_evaluation_runner(
        settings,
        engine=app.state.engine,
        session_factory=app.state.session_factory,
        embedder=app.state.embedder,
        llm=app.state.llm if body.generation else None,
        prompts=app.state.prompts,
    )
    options = EvaluationOptions(
        name=body.name,
        method=body.method,
        top_k=body.top_k,
        k=body.k,
        generation=body.generation,
        limit=body.limit,
        case_types=tuple(body.case_types),
    )
    async with app.state.evaluation_start_lock:
        if _running(app):
            raise EvaluationAlreadyRunningError()
        run_id = await runner.create_queued_run(options, dataset)
        app.state.evaluation_task = asyncio.create_task(
            _run_in_background(app, runner, options, dataset, run_id),
            name=f"evaluation-{run_id}",
        )
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise AppError("The evaluation run was not stored.")
    return _view(run)


async def _run_in_background(
    app: FastAPI,
    runner: EvaluationRunner,
    options: EvaluationOptions,
    dataset: EvaluationDataset,
    run_id: uuid.UUID,
) -> None:
    try:
        await runner.run(options, dataset, run_id=run_id)
    except Exception as exc:  # the run must never stay "queued" after a failed start
        logger.error("evaluation run failed", extra={"run_id": str(run_id)}, exc_info=exc)
        message = exc.message if isinstance(exc, AppError) else "Unexpected error."
        async with app.state.session_factory() as session:
            run = await session.get(EvaluationRun, run_id)
            if run is not None and run.status in (
                EvaluationRunStatus.QUEUED.value,
                EvaluationRunStatus.RUNNING.value,
            ):
                run.status = EvaluationRunStatus.FAILED.value
                run.error = message
                await session.commit()


@router.get(
    "/evaluations",
    responses=READ,
    response_model=EvaluationRunList,
    summary="Recent evaluation runs",
)
async def evaluations(
    request: Request,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationRunList:
    runs = await list_runs(session, limit)
    return EvaluationRunList(items=[_view(r) for r in runs], running=_running(request.app))


@router.get(
    "/evaluations/{evaluation_id}",
    responses=READ | errors(404),
    response_model=EvaluationRunDetail,
    summary="One evaluation run with per-case results",
)
async def evaluation(evaluation_id: uuid.UUID, session: SessionDep) -> EvaluationRunDetail:
    found = await get_run(session, evaluation_id)
    if found is None:
        raise NotFoundError(
            "Evaluation run not found.", details={"evaluation_id": str(evaluation_id)}
        )
    run, cases = found
    return EvaluationRunDetail(
        **_view(run).model_dump(),
        cases=[EvaluationCaseView.model_validate(c, from_attributes=True) for c in cases],
    )
