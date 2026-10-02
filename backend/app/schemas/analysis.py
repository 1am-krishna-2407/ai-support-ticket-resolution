"""Contracts for ticket analysis: LLM output schemas and the API response.

LLM output models (`TriageOutput`, `GeneratedResolution`, `ClaimCheckOutput`) are sent
to the provider as strict JSON schemas and validated again on return.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.categories import Category
from app.schemas.retrieval import RetrievalMethod

SOURCE_ID_PATTERN = r"^SRC-\d{3}$"
DRAFT_LABEL = "AI-generated draft. Not reviewed or approved by a human."


# --- LLM output: triage (classification + priority signals) ---------------------------


class ImpactLevel(StrEnum):
    NONE = "none"
    DEGRADED = "degraded"
    OUTAGE = "outage"


class UsersAffected(StrEnum):
    SINGLE = "single_user"
    TEAM = "team"
    MANY = "many_users"
    ALL = "all_users"


class PrioritySignals(BaseModel):
    """The eight rubric factors (docs/priority-rubric.md), extracted from the ticket text."""

    model_config = ConfigDict(extra="forbid")

    production_impact: ImpactLevel
    users_affected: UsersAffected
    security_concern: bool
    data_loss_risk: bool
    blocks_ci_cd_or_deployments: bool
    workaround_available: bool
    time_sensitive: bool
    regression: bool


class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    secondary_category: Category | None
    secondary_confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=500)
    signals: PrioritySignals


# --- LLM output: resolution ------------------------------------------------------------


class ResolutionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=1500)
    source_ids: list[str] = Field(max_length=10)


class GeneratedResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_type: Literal["resolution", "insufficient_evidence"]
    summary: str = Field(max_length=1000)
    resolution_steps: list[ResolutionStep] = Field(max_length=15)
    explanation: str = Field(max_length=2000)
    warnings: list[str] = Field(max_length=10)
    questions_for_customer: list[str] = Field(max_length=10)
    source_ids: list[str] = Field(max_length=20)
    confidence: Literal["low", "medium", "high"]
    grounded: bool


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_number: int = Field(ge=1)
    supported: bool
    reason: str = Field(max_length=300)


class ClaimCheckOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[ClaimVerdict]


# --- API response ----------------------------------------------------------------------


class ClassificationView(BaseModel):
    category: Category | None
    confidence: float | None
    secondary_category: Category | None
    secondary_confidence: float | None
    ambiguous: bool
    rationale: str | None


class PriorityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PriorityView(BaseModel):
    level: PriorityLevel | None
    explanation: str
    triggered_factors: list[str]
    signals: PrioritySignals | None
    is_estimate: bool = True


class Citation(BaseModel):
    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    title: str
    section: str | None
    url: str
    excerpt: str
    semantic_score: float
    final_score: float


class ResolutionStepView(BaseModel):
    number: int
    instruction: str
    source_ids: list[str]


ResolutionStatus = Literal["draft", "insufficient_evidence", "withheld", "unavailable"]


class ResolutionView(BaseModel):
    # draft: grounded steps; insufficient_evidence: documentation does not cover the ticket;
    # withheld: steps were generated but failed validation; unavailable: a provider failed.
    status: ResolutionStatus
    summary: str
    steps: list[ResolutionStepView]
    explanation: str
    warnings: list[str]
    questions_for_customer: list[str]
    confidence: Literal["low", "medium", "high"] | None


class GroundingCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class GroundingReport(BaseModel):
    grounded: bool
    score: float = Field(ge=0.0, le=1.0)
    checks: list[GroundingCheck]
    claim_level: bool


class ReviewReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    RETRIEVAL_DEGRADED = "retrieval_degraded"
    GROUNDING_FAILED = "grounding_failed"
    CITATION_INVALID = "citation_invalid"
    UNSUPPORTED_IDENTIFIERS = "unsupported_identifiers"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    LLM_UNAVAILABLE = "llm_unavailable"
    CLASSIFICATION_UNAVAILABLE = "classification_unavailable"
    LOW_CLASSIFICATION_CONFIDENCE = "low_classification_confidence"
    AMBIGUOUS_CATEGORY = "ambiguous_category"
    HIGH_PRIORITY = "high_or_critical_priority"
    SECURITY_CONCERN = "security_concern"
    DATA_LOSS_RISK = "data_loss_risk"
    NEEDS_MORE_INFORMATION = "needs_more_information"
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"


class HumanReviewDecision(BaseModel):
    required: bool
    reasons: list[ReviewReason]


class StageError(BaseModel):
    stage: str
    code: str
    message: str


class RetrievalSummary(BaseModel):
    method: RetrievalMethod
    evidence_sufficient: bool
    reason: str
    threshold: float
    candidates: int
    evidence_used: int
    excluded_for_injection: list[str]
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)


class ModelInfo(BaseModel):
    llm: str | None
    embedding: str | None
    prompt_versions: dict[str, str]


class TicketAnalysisResponse(BaseModel):
    label: str = DRAFT_LABEL
    # Set when the analysis was saved to the signed-in user's history.
    analysis_id: uuid.UUID | None = None
    classification: ClassificationView
    priority: PriorityView
    resolution: ResolutionView
    sources: list[Citation]
    grounding: GroundingReport
    human_review: HumanReviewDecision
    retrieval: RetrievalSummary
    errors: list[StageError]
    timings_ms: dict[str, float]
    model: ModelInfo


class ClassifyResponse(BaseModel):
    label: str = DRAFT_LABEL
    classification: ClassificationView
    priority: PriorityView
    human_review: HumanReviewDecision
    errors: list[StageError]
    timings_ms: dict[str, float]
    model: ModelInfo
