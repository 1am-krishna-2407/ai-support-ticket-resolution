from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DependencyStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class DependencyCheck(BaseModel):
    name: str
    status: DependencyStatus
    detail: str
    latency_ms: float | None = None


class ReadinessResponse(BaseModel):
    status: DependencyStatus
    checks: list[DependencyCheck]
    knowledge_base: dict[str, int] | None
