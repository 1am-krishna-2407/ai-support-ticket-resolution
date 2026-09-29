"""Citation and grounding validation, and the human-review decision.

These checks are deterministic and run on every generated draft. They are the backstop
against hallucinated citations, invented commands, and overconfident answers,
regardless of what the model claims about itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.schemas.analysis import (
    SOURCE_ID_PATTERN,
    GeneratedResolution,
    GroundingCheck,
    GroundingReport,
    HumanReviewDecision,
    PriorityLevel,
    ReviewReason,
)
from app.schemas.retrieval import RetrievedChunk

_SOURCE_ID = re.compile(SOURCE_ID_PATTERN)
_INLINE_CODE = re.compile(r"`([^`\n]{2,200})`")
_IDENTIFIERS = (
    re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{1,40})"),  # CLI flags
    re.compile(r"\$?\b((?:CI|GITLAB|DOCKER|RUNNER)_[A-Z0-9_]{2,})\b"),  # CI/CD variables
    re.compile(r"\b(gitlab-(?:runner|ctl|rake|backup)\s+[a-z][\w-]*)"),  # GitLab commands
    re.compile(r"(?<![\w/.])((?:/[\w.-]+){2,})"),  # absolute file paths
)
_VERSION = re.compile(r"\b(?:GitLab|Runner|version|v)\s*(\d{1,2}\.\d{1,2})(?:\.\d+)?\b", re.I)


@dataclass(frozen=True)
class CitationReport:
    malformed: tuple[str, ...]
    unknown: tuple[str, ...]
    steps_without_citations: tuple[int, ...]
    cited: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not (self.malformed or self.unknown or self.steps_without_citations)


def validate_citations(
    resolution: GeneratedResolution, evidence_ids: Iterable[str]
) -> CitationReport:
    allowed = set(evidence_ids)
    all_ids = [*resolution.source_ids] + [
        sid for s in resolution.resolution_steps for sid in s.source_ids
    ]
    malformed = tuple(dict.fromkeys(i for i in all_ids if not _SOURCE_ID.match(i)))
    unknown = tuple(dict.fromkeys(i for i in all_ids if _SOURCE_ID.match(i) and i not in allowed))
    missing = tuple(
        n for n, step in enumerate(resolution.resolution_steps, 1)
        if not [i for i in step.source_ids if i in allowed]
    )  # fmt: skip
    cited = tuple(dict.fromkeys(i for i in all_ids if i in allowed))
    return CitationReport(malformed, unknown, missing, cited)


def extract_identifiers(text: str) -> set[str]:
    """Commands, flags, variables, keys, and paths that must appear verbatim in evidence."""
    found = {m.group(1).strip() for m in _INLINE_CODE.finditer(text)}
    for pattern in _IDENTIFIERS:
        found.update(m.group(1).strip() for m in pattern.finditer(text))
    return {f for f in found if len(f) >= 2}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


@dataclass
class GroundingInputs:
    resolution: GeneratedResolution
    evidence: Sequence[RetrievedChunk]  # chunks actually given to the model
    evidence_sufficient: bool
    citations: CitationReport
    ticket_text: str
    high_confidence_min_sources: int = 2
    claim_verdicts: dict[int, bool] | None = None
    unsupported_identifiers: list[str] = field(default_factory=list)


def check_grounding(inputs: GroundingInputs) -> GroundingReport:
    r = inputs.resolution
    by_id = {c.source_id: c for c in inputs.evidence}
    checks: list[GroundingCheck] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(GroundingCheck(name=name, passed=passed, detail=detail))

    add("evidence_present", bool(by_id), f"{len(by_id)} source(s) provided to the model.")
    add(
        "retrieval_threshold",
        inputs.evidence_sufficient,
        "Retrieval met the relevance threshold."
        if inputs.evidence_sufficient
        else "Retrieval did not meet the relevance threshold.",
    )
    c = inputs.citations
    add(
        "citations_valid",
        not c.malformed and not c.unknown,
        "All cited IDs exist in the evidence set."
        if not c.malformed and not c.unknown
        else f"Invalid citations: {', '.join(c.malformed + c.unknown)}.",
    )
    add(
        "steps_cited",
        not c.steps_without_citations,
        "Every step cites evidence."
        if not c.steps_without_citations
        else f"Steps without valid citations: {list(c.steps_without_citations)}.",
    )

    # Identifiers in each step must appear in that step's cited evidence.
    unsupported: list[str] = []
    for step in r.resolution_steps:
        cited_text = _normalize(" ".join(by_id[i].content for i in step.source_ids if i in by_id))
        for ident in sorted(extract_identifiers(step.instruction)):
            if _normalize(ident) not in cited_text:
                unsupported.append(ident)
    inputs.unsupported_identifiers[:] = unsupported
    add(
        "identifiers_supported",
        not unsupported,
        "Commands, flags, and settings match the cited sources."
        if not unsupported
        else f"Not found in cited sources: {', '.join(unsupported[:10])}.",
    )

    # Version claims must come from the evidence or the ticket itself.
    evidence_text = " ".join(ch.content for ch in by_id.values()) + " " + inputs.ticket_text
    versions_in_steps = {
        m.group(1) for s in r.resolution_steps for m in _VERSION.finditer(s.instruction)
    }
    unsupported_versions = sorted(v for v in versions_in_steps if v not in evidence_text)
    add(
        "versions_supported",
        not unsupported_versions,
        "Version references match the evidence."
        if not unsupported_versions
        else f"Versions not in evidence: {', '.join(unsupported_versions)}.",
    )

    cited_count = len(c.cited)
    overconfident = r.confidence == "high" and (
        cited_count < inputs.high_confidence_min_sources or not inputs.evidence_sufficient
    )
    add(
        "confidence_calibrated",
        not overconfident,
        "Stated confidence is consistent with the evidence."
        if not overconfident
        else f"'high' confidence claimed with {cited_count} cited source(s).",
    )

    if inputs.claim_verdicts is not None:
        failing = sorted(n for n, ok in inputs.claim_verdicts.items() if not ok)
        add(
            "claims_supported",
            not failing,
            "Each step is supported by its cited sources (model-checked)."
            if not failing
            else f"Steps judged unsupported: {failing}.",
        )

    mandatory_ok = all(ch.passed for ch in checks)
    add(
        "grounded_flag_consistent",
        r.grounded == mandatory_ok or not r.grounded,
        "Model's grounded flag agrees with validation."
        if r.grounded == mandatory_ok or not r.grounded
        else "Model claimed grounded=true but validation failed.",
    )
    grounded = mandatory_ok and r.grounded and r.answer_type == "resolution"
    score = sum(ch.passed for ch in checks) / len(checks)
    return GroundingReport(
        grounded=grounded,
        score=round(score, 4),
        checks=checks,
        claim_level=inputs.claim_verdicts is not None,
    )


@dataclass(frozen=True)
class ReviewInputs:
    evidence_sufficient: bool
    retrieval_available: bool
    grounded: bool | None
    citations_valid: bool | None
    unsupported_identifiers: bool
    invalid_model_output: bool
    llm_unavailable: bool
    classification_available: bool
    classification_confidence: float | None
    ambiguous: bool
    min_confidence: float
    priority: PriorityLevel | None
    security_concern: bool
    data_loss_risk: bool
    questions_for_customer: bool
    injection_suspected: bool
    retrieval_degraded: bool = False


def decide_human_review(i: ReviewInputs) -> HumanReviewDecision:
    """Every trigger that applies is reported; any trigger means review is required."""
    reasons: list[ReviewReason] = []
    rules: list[tuple[bool, ReviewReason]] = [
        (not i.retrieval_available, ReviewReason.RETRIEVAL_UNAVAILABLE),
        (i.retrieval_degraded, ReviewReason.RETRIEVAL_DEGRADED),
        (i.retrieval_available and not i.evidence_sufficient, ReviewReason.INSUFFICIENT_EVIDENCE),
        (i.grounded is False, ReviewReason.GROUNDING_FAILED),
        (i.citations_valid is False, ReviewReason.CITATION_INVALID),
        (i.unsupported_identifiers, ReviewReason.UNSUPPORTED_IDENTIFIERS),
        (i.invalid_model_output, ReviewReason.INVALID_MODEL_OUTPUT),
        (i.llm_unavailable, ReviewReason.LLM_UNAVAILABLE),
        (not i.classification_available, ReviewReason.CLASSIFICATION_UNAVAILABLE),
        (
            i.classification_confidence is not None
            and i.classification_confidence < i.min_confidence,
            ReviewReason.LOW_CLASSIFICATION_CONFIDENCE,
        ),
        (i.ambiguous, ReviewReason.AMBIGUOUS_CATEGORY),
        (i.priority in (PriorityLevel.HIGH, PriorityLevel.CRITICAL), ReviewReason.HIGH_PRIORITY),
        (i.security_concern, ReviewReason.SECURITY_CONCERN),
        (i.data_loss_risk, ReviewReason.DATA_LOSS_RISK),
        (i.questions_for_customer, ReviewReason.NEEDS_MORE_INFORMATION),
        (i.injection_suspected, ReviewReason.PROMPT_INJECTION_SUSPECTED),
    ]
    for condition, reason in rules:
        if condition:
            reasons.append(reason)
    return HumanReviewDecision(required=bool(reasons), reasons=reasons)
