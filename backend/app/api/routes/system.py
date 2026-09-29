from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SettingsDep
from app.repositories import documents as doc_repo
from app.schemas.readiness import DependencyCheck, DependencyStatus, ReadinessResponse
from app.schemas.system import HealthResponse, VersionResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/version", response_model=VersionResponse, summary="Build and API version")
async def version(settings: SettingsDep) -> VersionResponse:
    return VersionResponse(
        name=settings.app_name,
        version=settings.app_version,
        api_version=settings.api_prefix.rsplit("/", 1)[-1],
        environment=settings.environment.value,
    )


def _provider_check(name: str, provider: object | None, label: str) -> DependencyCheck:
    if provider is None:
        return DependencyCheck(
            name=name, status=DependencyStatus.NOT_CONFIGURED, detail=f"{label} not configured."
        )
    failure = getattr(provider, "last_failure", None)
    if failure:
        return DependencyCheck(
            name=name,
            status=DependencyStatus.DEGRADED,
            detail=f"Last request failed ({failure.get('code')}): {failure.get('message')}",
        )
    return DependencyCheck(
        name=name,
        status=DependencyStatus.OK,
        detail=f"{label} configured; no failure since the last successful request "
        "(not probed, to avoid spending provider credits).",
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness of the API and its dependencies",
    responses={503: {"model": ReadinessResponse}},
)
async def ready(request: Request, settings: SettingsDep) -> JSONResponse:
    app = request.app
    checks = [DependencyCheck(name="api", status=DependencyStatus.OK, detail="Serving requests.")]
    kb: dict[str, int] | None = None

    started = time.perf_counter()
    try:
        async with app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_ms = round((time.perf_counter() - started) * 1000, 2)
            checks.append(
                DependencyCheck(
                    name="database",
                    status=DependencyStatus.OK,
                    detail="Connected.",
                    latency_ms=db_ms,
                )
            )
            vector = (
                await session.execute(
                    text(
                        "SELECT (SELECT extversion FROM pg_extension WHERE extname = 'vector'),"
                        " (SELECT count(*) FROM pg_indexes"
                        "   WHERE indexname = 'ix_document_chunks_embedding_hnsw'),"
                        " (SELECT atttypmod FROM pg_attribute"
                        "   WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding')"
                    )
                )
            ).one()
            kb = await doc_repo.knowledge_base_counts(session)
    except (SQLAlchemyError, OSError):
        checks.append(
            DependencyCheck(
                name="database",
                status=DependencyStatus.UNAVAILABLE,
                detail="Cannot reach database.",
            )
        )
        vector = None

    if vector is not None:
        extversion, hnsw, dim = vector
        if not extversion or not hnsw:
            checks.append(
                DependencyCheck(
                    name="vector_search",
                    status=DependencyStatus.UNAVAILABLE,
                    detail="pgvector extension or HNSW index missing; run migrations.",
                )
            )
        elif dim != settings.embedding_dimension:
            checks.append(
                DependencyCheck(
                    name="vector_search",
                    status=DependencyStatus.UNAVAILABLE,
                    detail=f"Column is vector({dim}) but EMBEDDING_DIMENSION is "
                    f"{settings.embedding_dimension}; run migrations.",
                )
            )
        else:
            checks.append(
                DependencyCheck(
                    name="vector_search",
                    status=DependencyStatus.OK,
                    detail=f"pgvector {extversion}, HNSW index on vector({dim}).",
                )
            )
    if kb is not None:
        if kb["documents"] == 0:
            kb_status, kb_detail = (
                DependencyStatus.UNAVAILABLE,
                "No active documents; run ingestion.",
            )
        elif kb["embedded_chunks"] < kb["chunks"]:
            kb_status = DependencyStatus.DEGRADED
            kb_detail = (
                f"{kb['documents']} documents; {kb['chunks'] - kb['embedded_chunks']} of "
                f"{kb['chunks']} chunks lack embeddings."
            )
        else:
            kb_status = DependencyStatus.OK
            kb_detail = f"{kb['documents']} documents, {kb['chunks']} chunks, all embedded."
        checks.append(DependencyCheck(name="knowledge_base", status=kb_status, detail=kb_detail))

    checks.append(_provider_check("embedding_provider", app.state.embedder, "Embedding provider"))
    checks.append(_provider_check("llm_provider", app.state.llm, "LLM provider"))

    database_down = any(
        c.name == "database" and c.status is DependencyStatus.UNAVAILABLE for c in checks
    )
    if database_down:
        overall = DependencyStatus.UNAVAILABLE
    elif all(c.status is DependencyStatus.OK for c in checks):
        overall = DependencyStatus.OK
    else:
        overall = DependencyStatus.DEGRADED
    body = ReadinessResponse(status=overall, checks=checks, knowledge_base=kb)
    return JSONResponse(
        status_code=503 if database_down else 200, content=body.model_dump(mode="json")
    )
