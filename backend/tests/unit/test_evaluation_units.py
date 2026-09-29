from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.services.evaluation.dataset import EvaluationDataset, load_dataset
from app.services.evaluation.metrics import CaseOutcome, aggregate, case_metrics, percentile
from app.services.evaluation.retrieval_metrics import (
    hit_rate_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.services.ingestion.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[3]


# --- the real dataset ------------------------------------------------------------------------


def test_repository_dataset_is_valid_and_sources_exist() -> None:
    dataset = load_dataset(ROOT / "evaluation" / "cases.json")
    manifest = load_manifest(ROOT / "knowledge" / "manifest" / "sources.json", ["docs.gitlab.com"])
    assert dataset.unknown_sources(e.document_id for e in manifest) == {}
    assert len(dataset.cases) >= 40
    assert len({c.type for c in dataset.cases}) == 17
    behaviors = {c.expected_behavior for c in dataset.cases}
    assert behaviors == {"answer", "insufficient_evidence", "resist_injection"}


def test_golden_queries_reference_real_documents() -> None:
    golden = json.loads((ROOT / "evaluation" / "golden_queries.json").read_text(encoding="utf-8"))
    manifest = {
        e.document_id
        for e in load_manifest(ROOT / "knowledge/manifest/sources.json", ["docs.gitlab.com"])
    }
    for q in golden["queries"]:
        assert set(q["expected_documents"]) <= manifest, q["id"]


def _case(**overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": "x1", "type": "t", "title": "Some title", "description": "A long enough description",
        "expected_category": "runners", "expected_priority": "low", "expected_sources": ["d"],
        "expected_behavior": "answer",
    }  # fmt: skip
    case.update(overrides)
    return case


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"expected_behavior": "insufficient_evidence"}, "must not list expected sources"),
        ({"expected_sources": []}, "need expected sources"),
        ({"expected_behavior": "resist_injection"}, "forbidden_markers"),
        ({"expected_category": "billing"}, "expected_category"),
        ({"expected_priority": "urgent"}, "expected_priority"),
        ({"unexpected": 1}, "unexpected"),
    ],
)
def test_invalid_cases_are_rejected(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EvaluationDataset.model_validate(
            {"description": "d", "version": "1", "cases": [_case(**overrides)]}
        )


def test_duplicate_case_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        EvaluationDataset.model_validate(
            {"description": "d", "version": "1", "cases": [_case(), _case()]}
        )


# --- metric formulas (hand-computed) ------------------------------------------------------------


def test_retrieval_metric_formulas() -> None:
    retrieved = ["a", "x", "b", "y", "c"]
    assert recall_at_k(retrieved, ["a", "b", "z"], 3) == pytest.approx(2 / 3)
    assert hit_rate_at_k(retrieved, ["z", "c"], 5) == 1.0
    assert hit_rate_at_k(retrieved, ["c"], 4) == 0.0
    assert reciprocal_rank(retrieved, ["b"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(retrieved, ["q"]) == 0.0
    assert precision_at_k(retrieved, ["a", "b"], 4) == pytest.approx(2 / 4)
    with pytest.raises(ValueError):
        recall_at_k(retrieved, [], 5)


def test_percentile_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 100.0
    assert percentile([], 95) == 0.0


def outcome(**overrides: Any) -> CaseOutcome:
    base: dict[str, Any] = {
        "case_id": "c", "case_type": "t", "expected_behavior": "answer",
        "expected_documents": ["a"], "retrieved_documents": ["a", "b"],
        "evidence_sufficient": True, "latency_ms": 100.0,
    }  # fmt: skip
    base.update(overrides)
    return CaseOutcome(**base)


def test_case_metrics_priority_and_citations() -> None:
    m = case_metrics(
        outcome(
            expected_priority="high",
            priority="medium",
            expected_category="runners",
            category="runners",
            cited_documents=["a", "z"],
        ),
        k=5,
    )
    assert m["priority_exact"] is False and m["priority_within_one"] is True
    assert m["category_correct"] is True
    assert m["citation_precision"] == 0.5
    assert m["recall@5"] == 1.0 and m["reciprocal_rank"] == 1.0


def test_aggregate_refusals_injection_and_latency() -> None:
    outcomes = [
        outcome(case_id="a1", latency_ms=100),
        outcome(case_id="a2", retrieved_documents=["q"], evidence_sufficient=False, latency_ms=300),
        outcome(case_id="n1", expected_behavior="insufficient_evidence", expected_documents=[],
                evidence_sufficient=False, latency_ms=200),
        outcome(case_id="n2", expected_behavior="insufficient_evidence", expected_documents=[],
                evidence_sufficient=True, latency_ms=400),
        outcome(case_id="i1", expected_behavior="resist_injection", injection_flagged=True),
        outcome(case_id="i2", expected_behavior="resist_injection", injection_flagged=True,
                leaked_markers=["rm -rf"]),
    ]  # fmt: skip
    s = aggregate(outcomes, 5)["summary"]
    d = s["deterministic"]
    assert d["correct_refusal_rate"] == 0.5  # n1 refused, n2 answered
    assert d["false_refusal_rate"] == 0.5  # a2 refused although answerable
    assert d["injection_resisted_rate"] == 0.5  # i2 leaked a forbidden marker
    assert d["hit_rate@5"] == pytest.approx(3 / 4)  # a1, i1, i2 hit; a2 missed
    assert s["latency_ms"]["median"] == 100.0 and s["cases"] == 6
    assert s["llm_judged"] == {}


def test_generation_statuses_drive_refusal_when_present() -> None:
    o = outcome(expected_behavior="insufficient_evidence", expected_documents=[],
                evidence_sufficient=True, resolution_status="insufficient_evidence")  # fmt: skip
    assert aggregate([o], 5)["summary"]["deterministic"]["correct_refusal_rate"] == 1.0
