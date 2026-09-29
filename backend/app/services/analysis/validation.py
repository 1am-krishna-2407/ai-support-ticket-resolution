"""Citation and grounding validation, and the human-review decision.

These checks are deterministic and run on every generated draft. They are the backstop
against hallucinated citations, invented commands, and overconfident answers,
regardless of what the model claims about itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

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


# Placeholders and example values that stand in for the customer's own data.
_PLACEHOLDER = re.compile(
    r"<[^<>\n]{1,60}>|\{\{[^}]{1,60}\}\}|\b(?:your|my)[-_][\w.-]+|\byour\w*\b"
    r"|\bexample\.(?:com|org|net)\b|/path/to/[\w./-]*|\bxxx+\b",
    re.IGNORECASE,
)
# `key = value`, `KEY=value`, `--flag=value`, `Header: value` → the key is the identifier.
_KEY_VALUE = re.compile(r"^\s*(--?[A-Za-z][\w.-]*|[A-Za-z_][\w.-]*)\s*(?:=|:)\s*\S")


def identifier_alternatives(span: str, ticket_text: str = "") -> tuple[str, ...]:
    """What must appear in evidence for `span` to count as supported (any alternative).

    Returns () when there is nothing to check (URLs, pure placeholders). The customer's
    own values (hostnames, sizes, paths under placeholders) are not required to appear;
    the documented key, flag, command, or directory is.
    """
    s = span.strip().strip(".,;")
    if not s or s.lower().startswith(("http://", "https://")):
        return ()
    match = _KEY_VALUE.match(s)
    if match and not s.startswith(("/", "~")):
        return (match.group(1),)
    if s.startswith(("/path/to/", "path/to/")):
        return ()  # a pure placeholder path
    if s.startswith(("/", "~/")):
        directory = s[: s.rstrip("/").rfind("/") + 1]
        name = s.rstrip("/").rsplit("/", 1)[-1]
        # Customer-specific: a placeholder, or a hostname-like name taken from the ticket
        # (gitlab.internal.crt). Generic names such as gitlab.rb must appear verbatim.
        base = name.rsplit(".", 1)[0].lower()
        customer_specific = bool(_PLACEHOLDER.search(name)) or (
            "." in base and base in ticket_text.lower()
        )
        if customer_specific and len(directory) > 1:
            # e.g. /etc/gitlab-runner/certs/<host>.crt: the documented directory is enough.
            return (s, directory)
        return (s,)
    if _PLACEHOLDER.search(s):
        cleaned = _PLACEHOLDER.sub(" ", s).strip(" :=/-_")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return (cleaned,) if len(cleaned) >= 3 else ()
    return (s,)


def extract_identifiers(text: str) -> set[str]:
    """Inline code spans and command/flag/variable/path mentions in `text`."""
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
    high_confidence_min_score: float = 0.75
    claim_verdicts: dict[int, bool] | None = None
    unsupported_identifiers: list[str] = field(default_factory=list)
    # Set when a stated "high" confidence is not backed by the evidence and is lowered.
    adjusted_confidence: Literal["medium"] | None = None


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

    # Commands, flags, keys, and documented paths in each step must appear in the evidence
    # the draft cites: the step's own sources first, then any source the draft cites.
    all_cited = _normalize(" ".join(by_id[i].content for i in c.cited if i in by_id))
    ticket = _normalize(inputs.ticket_text)
    unsupported: list[str] = []
    for step in r.resolution_steps:
        step_text = _normalize(" ".join(by_id[i].content for i in step.source_ids if i in by_id))
        for ident in sorted(extract_identifiers(step.instruction)):
            alternatives = identifier_alternatives(ident, inputs.ticket_text)
            if not alternatives:
                continue
            if not any(
                _normalize(a) in step_text or _normalize(a) in all_cited or _normalize(a) in ticket
                for a in alternatives
            ):
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

    # "high" needs sufficient evidence and either several cited sources or one strong one.
    # An unsupported "high" is lowered to "medium" (reported) rather than withholding the
    # draft: the label, not the content, is what was overstated.
    cited_count = len(c.cited)
    best = max((by_id[i].scores.final for i in c.cited if i in by_id), default=0.0)
    backed = inputs.evidence_sufficient and (
        cited_count >= inputs.high_confidence_min_sources
        or best >= inputs.high_confidence_min_score
    )
    if r.confidence == "high" and not backed:
        inputs.adjusted_confidence = "medium"
        add(
            "confidence_calibrated",
            True,
            f"Stated 'high' lowered to 'medium': {cited_count} cited source(s), "
            f"best similarity {best:.2f}.",
        )
    else:
        add("confidence_calibrated", True, "Stated confidence is consistent with the evidence.")

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
