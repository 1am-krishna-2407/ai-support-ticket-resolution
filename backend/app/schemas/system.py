from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness only: the API process is up and serving requests.

    Dependency readiness (database, pgvector, knowledge base) is reported by
    `GET /ready`.
    """

    status: Literal["ok"]


class VersionResponse(BaseModel):
    name: str
    version: str
    api_version: str
    environment: str
