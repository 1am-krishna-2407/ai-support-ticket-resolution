"""Evaluation run contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.retrieval import RetrievalMethod


class EvaluationRunRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    method: RetrievalMethod = RetrievalMethod.HYBRID
    top_k: int | None = Field(default=None, ge=1, le=50)
    k: int = Field(default=5, ge=1, le=20)
    generation: bool = False
    limit: int | None = Field(default=None, ge=1, le=500)
    case_types: list[str] = Field(default_factory=list, max_length=50)


class EvaluationRunView(BaseModel):
    id: uuid.UUID
    name: str | None
    status: str
    config: dict[str, Any]
    metrics: dict[str, Any] | None
    dataset_version: str | None
    total_cases: int
    completed_cases: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EvaluationCaseView(BaseModel):
    case_id: str
    case_type: str
    expected: dict[str, Any]
    actual: dict[str, Any] | None
    metrics: dict[str, Any] | None
    passed: bool | None
    latency_ms: float | None
    error: str | None


class EvaluationRunDetail(EvaluationRunView):
    cases: list[EvaluationCaseView]


class EvaluationRunList(BaseModel):
    items: list[EvaluationRunView]
    running: bool
