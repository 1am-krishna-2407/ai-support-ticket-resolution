from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SettingsDep
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
