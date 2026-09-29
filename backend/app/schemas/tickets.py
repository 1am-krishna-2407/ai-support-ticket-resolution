"""Ticket input contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.retrieval import RetrievalMethod

TITLE_MAX_CHARS = 300
DESCRIPTION_MAX_CHARS = 20_000


class TicketEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gitlab_version: str | None = Field(default=None, max_length=50)
    deployment: Literal["gitlab.com", "self-managed", "dedicated"] | None = None
    runner_executor: str | None = Field(default=None, max_length=50)
    component: str | None = Field(default=None, max_length=100)


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=TITLE_MAX_CHARS)
    description: str = Field(min_length=10, max_length=DESCRIPTION_MAX_CHARS)
    environment: TicketEnvironment | None = None


class TicketRetrieveRequest(TicketInput):
    method: RetrievalMethod = RetrievalMethod.HYBRID
    top_k: int | None = Field(default=None, ge=1, le=50)
