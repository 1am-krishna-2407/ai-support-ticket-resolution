"""Evaluation dataset loading and validation (evaluation/cases.json)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.categories import Category
from app.schemas.analysis import PriorityLevel
from app.schemas.tickets import TicketEnvironment

Behavior = Literal["answer", "insufficient_evidence", "resist_injection"]


class EvaluationCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=10, max_length=20_000)
    environment: TicketEnvironment | None = None
    expected_category: Category | None
    expected_priority: PriorityLevel | None
    expected_sources: list[str]
    expected_behavior: Behavior
    tags: list[str] = Field(default_factory=list)
    forbidden_markers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationCaseSpec:
        if self.expected_behavior == "insufficient_evidence" and self.expected_sources:
            raise ValueError(f"{self.id}: out-of-scope cases must not list expected sources")
        if self.expected_behavior != "insufficient_evidence" and not self.expected_sources:
            raise ValueError(f"{self.id}: answerable cases need expected sources")
        if self.expected_behavior == "resist_injection" and not self.forbidden_markers:
            raise ValueError(f"{self.id}: injection cases need forbidden_markers")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    version: str
    cases: list[EvaluationCaseSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> EvaluationDataset:
        ids = [c.id for c in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {duplicates}")
        return self

    def unknown_sources(self, known_document_ids: Iterable[str]) -> dict[str, list[str]]:
        known = set(known_document_ids)
        missing = {c.id: [s for s in c.expected_sources if s not in known] for c in self.cases}
        return {case_id: sources for case_id, sources in missing.items() if sources}


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
