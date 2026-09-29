"""Evaluation runner: dataset → retrieval (→ analysis) → metrics → persisted run.

Every run stores its frozen configuration (retrieval, chunking, models, prompt versions,
dataset version) so results stay comparable and reproducible. Only one evaluation runs
at a time (Postgres advisory lock). With `generation` off, no LLM is called; with it on,
each case costs one triage and one generation call.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, EvaluationAlreadyRunningError
from app.db.models import EvaluationCase, EvaluationRun, EvaluationRunStatus
from app.schemas.retrieval import RetrievalMethod, RetrievalResult
from app.schemas.tickets import TicketInput
from app.services.analysis.context import looks_like_injection
from app.services.analysis.service import TicketAnalysisService
from app.services.evaluation.dataset import EvaluationCaseSpec, EvaluationDataset
from app.services.evaluation.metrics import CaseOutcome, aggregate, case_metrics
from app.services.retrieval.query import transform_query
from app.services.retrieval.service import RetrievalService

logger = logging.getLogger("app.evaluation")

EVALUATION_LOCK_KEY = 7_300_002
ACTIVE_STATES = (EvaluationRunStatus.QUEUED.value, EvaluationRunStatus.RUNNING.value)


@dataclass(frozen=True)
class EvaluationOptions:
    name: str | None = None
    method: RetrievalMethod = RetrievalMethod.HYBRID
    top_k: int | None = None
    k: int = 5
    generation: bool = False
    limit: int | None = None
    case_types: tuple[str, ...] = ()


@dataclass
class _Progress:
    outcomes: list[CaseOutcome] = field(default_factory=list)


@asynccontextmanager
async def evaluation_lock(engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.connect() as conn:
        acquired = await conn.scalar(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": EVALUATION_LOCK_KEY}
        )
        await conn.commit()
        if not acquired:
            raise EvaluationAlreadyRunningError()
        try:
            yield
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": EVALUATION_LOCK_KEY})
            await conn.commit()


def frozen_config(
    settings: Settings,
    options: EvaluationOptions,
    dataset: EvaluationDataset,
    *,
    embedding_spec: str | None,
    llm_model: str | None,
    prompt_versions: dict[str, str],
) -> dict[str, Any]:
    return {
        "name": options.name,
        "dataset_version": dataset.version,
        "method": options.method.value,
        "top_k": options.top_k or settings.retrieval_top_k,
        "k": options.k,
        "candidate_k": settings.retrieval_candidate_k,
        "rrf_k": settings.retrieval_rrf_k,
        "weights": {
            "semantic": settings.retrieval_semantic_weight,
            "lexical": settings.retrieval_lexical_weight,
        },
        "relevance_threshold": settings.retrieval_relevance_threshold,
        "reranker_enabled": settings.reranker_enabled,
        "chunking": {
            "target_tokens": settings.chunk_target_tokens,
            "overlap_tokens": settings.chunk_overlap_tokens,
            "hard_max_tokens": settings.chunk_hard_max_tokens,
        },
        "embedding_spec": embedding_spec,
        "generation": options.generation,
        "llm_model": llm_model if options.generation else None,
        "llm_temperature": settings.llm_temperature if options.generation else None,
        "llm_seed": settings.llm_seed if options.generation else None,
        "prompt_versions": prompt_versions if options.generation else {},
        "limit": options.limit,
        "case_types": list(options.case_types),
    }


class EvaluationRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        retrieval: RetrievalService,
        analysis: TicketAnalysisService | None,
        embedding_spec: str | None,
        llm_model: str | None,
        prompt_versions: dict[str, str],
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._sessions = session_factory
        self._retrieval = retrieval
        self._analysis = analysis
        self._embedding_spec = embedding_spec
        self._llm_model = llm_model
        self._prompt_versions = prompt_versions

    async def create_queued_run(
        self, options: EvaluationOptions, dataset: EvaluationDataset
    ) -> uuid.UUID:
        async with self._sessions() as session:
            run = EvaluationRun(
                name=options.name,
                status=EvaluationRunStatus.QUEUED.value,
                config=self._config(options, dataset),
                dataset_version=dataset.version,
                total_cases=len(self._select(dataset, options)),
                completed_cases=0,
            )
            session.add(run)
            await session.commit()
            return run.id

    async def run(
        self,
        options: EvaluationOptions,
        dataset: EvaluationDataset,
        *,
        run_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if options.generation and self._analysis is None:
            raise AppError("Generation was requested but no LLM provider is configured.")
        async with evaluation_lock(self._engine):
            return await self._run_locked(options, dataset, run_id)

    # --- internals -----------------------------------------------------------------------

    def _config(self, options: EvaluationOptions, dataset: EvaluationDataset) -> dict[str, Any]:
        return frozen_config(
            self._settings,
            options,
            dataset,
            embedding_spec=self._embedding_spec,
            llm_model=self._llm_model,
            prompt_versions=self._prompt_versions,
        )

    @staticmethod
    def _select(dataset: EvaluationDataset, options: EvaluationOptions) -> list[EvaluationCaseSpec]:
        cases = [c for c in dataset.cases if not options.case_types or c.type in options.case_types]
        return cases[: options.limit] if options.limit else cases

    async def _run_locked(
        self, options: EvaluationOptions, dataset: EvaluationDataset, run_id: uuid.UUID | None
    ) -> uuid.UUID:
        cases = self._select(dataset, options)
        async with self._sessions() as session:
            # Runs left active by a dead process can never finish: fail them first.
            await session.execute(
                update(EvaluationRun)
                .where(
                    EvaluationRun.status.in_(ACTIVE_STATES),
                    *([EvaluationRun.id != run_id] if run_id else []),
                )
                .values(
                    status=EvaluationRunStatus.FAILED.value,
                    error="Interrupted before completion.",
                    finished_at=datetime.now(UTC),
                )
            )
            if run_id is None:
                run = EvaluationRun(
                    name=options.name,
                    status=EvaluationRunStatus.RUNNING.value,
                    config=self._config(options, dataset),
                    dataset_version=dataset.version,
                    total_cases=len(cases),
                    completed_cases=0,
                    started_at=datetime.now(UTC),
                )
                session.add(run)
                await session.commit()
                run_id = run.id
            else:
                await session.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id == run_id)
                    .values(status=EvaluationRunStatus.RUNNING.value, started_at=datetime.now(UTC))
                )
                await session.commit()

            progress = _Progress()
            try:
                for index, spec in enumerate(cases, 1):
                    outcome, actual = await self._evaluate(spec, options)
                    progress.outcomes.append(outcome)
                    metrics = case_metrics(outcome, options.k)
                    session.add(
                        EvaluationCase(
                            run_id=run_id,
                            case_id=spec.id,
                            case_type=spec.type,
                            expected={
                                "category": spec.expected_category,
                                "priority": spec.expected_priority,
                                "sources": spec.expected_sources,
                                "behavior": spec.expected_behavior,
                                "title": spec.title,
                            },
                            actual=actual,
                            metrics=metrics,
                            passed=self._passed(outcome, metrics, options),
                            latency_ms=outcome.latency_ms,
                            error=outcome.error,
                        )
                    )
                    await session.execute(
                        update(EvaluationRun)
                        .where(EvaluationRun.id == run_id)
                        .values(completed_cases=index)
                    )
                    await session.commit()
                result = aggregate(progress.outcomes, options.k)
                await session.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id == run_id)
                    .values(
                        status=EvaluationRunStatus.COMPLETED.value,
                        metrics=result["summary"] | {"by_type": self._by_type(progress, options.k)},
                        finished_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                await session.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id == run_id)
                    .values(
                        status=EvaluationRunStatus.FAILED.value,
                        error=f"{type(exc).__name__}: {exc}"[:1000],
                        finished_at=datetime.now(UTC),
                    )
                )
                await session.commit()
                raise
        return run_id

    @staticmethod
    def _by_type(progress: _Progress, k: int) -> dict[str, dict[str, Any]]:
        types: dict[str, list[CaseOutcome]] = {}
        for outcome in progress.outcomes:
            types.setdefault(outcome.case_type, []).append(outcome)
        return {
            name: aggregate(items, k)["summary"]["deterministic"] for name, items in types.items()
        }

    @staticmethod
    def _passed(outcome: CaseOutcome, metrics: dict[str, Any], options: EvaluationOptions) -> bool:
        if outcome.error:
            return False
        if outcome.expected_behavior == "insufficient_evidence":
            if outcome.resolution_status is not None:
                return outcome.resolution_status == "insufficient_evidence"
            return not outcome.evidence_sufficient
        if outcome.expected_behavior == "resist_injection":
            ok = bool(metrics.get(f"hit@{options.k}"))
            if options.generation:
                ok = ok and bool(outcome.injection_flagged) and not outcome.leaked_markers
            return ok
        ok = bool(metrics.get(f"hit@{options.k}")) and outcome.evidence_sufficient
        if options.generation:
            ok = ok and outcome.resolution_status == "draft" and bool(outcome.grounded)
        return ok

    async def _evaluate(
        self, spec: EvaluationCaseSpec, options: EvaluationOptions
    ) -> tuple[CaseOutcome, dict[str, Any]]:
        started = time.perf_counter()
        outcome = CaseOutcome(
            case_id=spec.id,
            case_type=spec.type,
            expected_behavior=spec.expected_behavior,
            expected_documents=list(spec.expected_sources),
            retrieved_documents=[],
            evidence_sufficient=False,
            latency_ms=0.0,
            expected_category=spec.expected_category.value if spec.expected_category else None,
            expected_priority=spec.expected_priority.value if spec.expected_priority else None,
        )
        actual: dict[str, Any] = {}
        try:
            query = transform_query(
                spec.title, spec.description, max_chars=self._settings.retrieval_max_query_chars
            )
            retrieval = await self._retrieval.retrieve(
                query, method=options.method, top_k=options.top_k
            )
            outcome.retrieved_documents = _documents(retrieval)
            outcome.evidence_sufficient = retrieval.evidence_sufficient
            actual["retrieval"] = {
                "documents": outcome.retrieved_documents[:10],
                "evidence_sufficient": retrieval.evidence_sufficient,
                "degraded": retrieval.degraded,
                "best_semantic": max((r.scores.semantic for r in retrieval.results), default=None),
            }
            if options.generation and self._analysis is not None:
                ticket = TicketInput(
                    title=spec.title, description=spec.description, environment=spec.environment
                )
                analysis = await self._analysis.analyze(ticket, precomputed_retrieval=retrieval)
                cited = {s.source_id: s for s in analysis.sources}
                by_source = {r.source_id: r.document_id for r in retrieval.results}
                outcome.category = analysis.classification.category
                outcome.priority = analysis.priority.level
                outcome.resolution_status = analysis.resolution.status
                outcome.grounded = analysis.grounding.grounded
                outcome.schema_valid = not any(
                    e.code == "INVALID_MODEL_OUTPUT" for e in analysis.errors
                )
                outcome.cited_documents = [by_source[s] for s in cited if s in by_source]
                outcome.injection_flagged = (
                    "prompt_injection_suspected" in analysis.human_review.reasons
                )
                text_out = analysis.model_dump_json()
                outcome.leaked_markers = [m for m in spec.forbidden_markers if m in text_out]
                actual["analysis"] = {
                    "category": outcome.category,
                    "priority": outcome.priority,
                    "status": outcome.resolution_status,
                    "grounded": outcome.grounded,
                    "failed_checks": [c.name for c in analysis.grounding.checks if not c.passed],
                    "cited_documents": outcome.cited_documents,
                    "review_reasons": list(analysis.human_review.reasons),
                    "errors": [f"{e.stage}:{e.code}" for e in analysis.errors],
                    "leaked_markers": outcome.leaked_markers,
                }
            elif spec.expected_behavior == "resist_injection":
                outcome.injection_flagged = looks_like_injection(spec.description)
        except AppError as exc:
            outcome.error = f"{exc.code.value}: {exc.message}"
        outcome.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return outcome, actual


def _documents(result: RetrievalResult) -> list[str]:
    ordered: list[str] = []
    for r in result.results:
        if r.document_id not in ordered:
            ordered.append(r.document_id)
    return ordered


async def list_runs(session: AsyncSession, limit: int = 20) -> list[EvaluationRun]:
    return list(
        await session.scalars(
            select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
        )
    )


async def get_run(
    session: AsyncSession, run_id: uuid.UUID
) -> tuple[EvaluationRun, list[EvaluationCase]] | None:
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        return None
    cases = list(
        await session.scalars(
            select(EvaluationCase)
            .where(EvaluationCase.run_id == run_id)
            .order_by(EvaluationCase.case_id)
        )
    )
    return run, cases
