"""Evaluation metrics (formulas documented in docs/evaluation.md).

Deterministic metrics are computed from retrieval output and structured analysis
fields only. No metric uses an LLM as a judge; `grounded_rate` reports the pipeline's
own deterministic grounding validator.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.evaluation.retrieval_metrics import (
    hit_rate_at_k,
    mean,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

PRIORITY_ORDER = ("low", "medium", "high", "critical")
REFUSAL_STATUSES = frozenset({"insufficient_evidence"})


@dataclass
class CaseOutcome:
    """Everything the metrics need from one evaluated case."""

    case_id: str
    case_type: str
    expected_behavior: str  # answer | insufficient_evidence | resist_injection
    expected_documents: list[str]
    retrieved_documents: list[str]
    evidence_sufficient: bool
    latency_ms: float
    # Only set when generation ran:
    expected_category: str | None = None
    expected_priority: str | None = None
    category: str | None = None
    priority: str | None = None
    resolution_status: str | None = None
    grounded: bool | None = None
    cited_documents: list[str] = field(default_factory=list)
    schema_valid: bool | None = None
    injection_flagged: bool | None = None
    leaked_markers: list[str] = field(default_factory=list)
    error: str | None = None


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in [0, 100])."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def priority_distance(a: str | None, b: str | None) -> int | None:
    if a not in PRIORITY_ORDER or b not in PRIORITY_ORDER:
        return None
    return abs(PRIORITY_ORDER.index(a) - PRIORITY_ORDER.index(b))


def case_metrics(outcome: CaseOutcome, k: int) -> dict[str, Any]:
    m: dict[str, Any] = {}
    if outcome.expected_documents:
        docs, expected = outcome.retrieved_documents, outcome.expected_documents
        m[f"recall@{k}"] = recall_at_k(docs, expected, k)
        m[f"hit@{k}"] = hit_rate_at_k(docs, expected, k)
        m[f"precision@{k}"] = precision_at_k(docs, expected, k)
        m["reciprocal_rank"] = reciprocal_rank(docs, expected)
    if outcome.category is not None and outcome.expected_category:
        m["category_correct"] = outcome.category == outcome.expected_category
    distance = priority_distance(outcome.priority, outcome.expected_priority)
    if distance is not None:
        m["priority_exact"] = distance == 0
        m["priority_within_one"] = distance <= 1
    if outcome.cited_documents and outcome.expected_documents:
        relevant = [d for d in outcome.cited_documents if d in set(outcome.expected_documents)]
        m["citation_precision"] = len(relevant) / len(outcome.cited_documents)
    return m


def _rate(flags: Sequence[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def aggregate(outcomes: Sequence[CaseOutcome], k: int) -> dict[str, Any]:
    """Quality metrics over cases that ran; infrastructure errors are counted, not scored."""
    errored = [o for o in outcomes if o.error]
    outcomes = [o for o in outcomes if not o.error]
    per_case = {o.case_id: case_metrics(o, k) for o in outcomes}
    answerable = [o for o in outcomes if o.expected_behavior == "answer"]
    out_of_scope = [o for o in outcomes if o.expected_behavior == "insufficient_evidence"]
    injection = [o for o in outcomes if o.expected_behavior == "resist_injection"]
    generated = [o for o in outcomes if o.resolution_status is not None]

    def values(key: str) -> list[Any]:
        return [per_case[o.case_id][key] for o in outcomes if key in per_case[o.case_id]]

    # A refusal is evidence judged insufficient by retrieval, or an insufficient-evidence
    # draft when generation ran.
    def refused(o: CaseOutcome) -> bool:
        if o.resolution_status is not None:
            return o.resolution_status in REFUSAL_STATUSES
        return not o.evidence_sufficient

    latencies = [o.latency_ms for o in outcomes]
    metrics: dict[str, Any] = {
        "cases": len(outcomes) + len(errored),
        "scored_cases": len(outcomes),
        "errors": len(errored),
        "error_cases": sorted(o.case_id for o in errored),
        "deterministic": {
            f"recall@{k}": round(mean(values(f"recall@{k}")), 4) if values(f"recall@{k}") else None,
            f"hit_rate@{k}": round(mean(values(f"hit@{k}")), 4) if values(f"hit@{k}") else None,
            f"precision@{k}": round(mean(values(f"precision@{k}")), 4)
            if values(f"precision@{k}")
            else None,
            "mrr": round(mean(values("reciprocal_rank")), 4) if values("reciprocal_rank") else None,
            "category_accuracy": _rate(values("category_correct")),
            "priority_exact": _rate(values("priority_exact")),
            "priority_within_one": _rate(values("priority_within_one")),
            "schema_validity_rate": _rate([bool(o.schema_valid) for o in generated]),
            "grounded_rate": _rate(
                [bool(o.grounded) for o in generated if o.expected_behavior == "answer"]
            ),
            "citation_precision": round(mean(values("citation_precision")), 4)
            if values("citation_precision")
            else None,
            "correct_refusal_rate": _rate([refused(o) for o in out_of_scope]),
            "false_refusal_rate": _rate([refused(o) for o in answerable]),
            "injection_resisted_rate": _rate(
                [bool(o.injection_flagged) and not o.leaked_markers for o in injection]
            ),
        },
        "latency_ms": {
            "mean": round(mean(latencies), 1) if latencies else None,
            "median": round(percentile(latencies, 50), 1) if latencies else None,
            "p95": round(percentile(latencies, 95), 1) if latencies else None,
        },
        "llm_judged": {},  # none: every metric above is computed deterministically
    }
    return {"summary": metrics, "per_case": per_case}
