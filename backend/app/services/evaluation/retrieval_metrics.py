"""Deterministic retrieval metrics (formulas in docs/evaluation.md).

Relevance is judged at document level: a retrieved chunk is relevant when its
document_id is one of the query's expected documents.

- Recall@K    = |relevant documents found in the top K| / |expected documents|
- HitRate@K   = 1 if any relevant document is in the top K, else 0
- MRR         = 1 / rank of the first relevant result (0 if none)
- Precision@K = relevant results in the top K / K (over results actually returned)
"""

from __future__ import annotations

from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    if not expected:
        raise ValueError("recall is undefined without expected documents")
    found = {d for d in retrieved[:k] if d in set(expected)}
    return len(found) / len(set(expected))


def hit_rate_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    return 1.0 if any(d in set(expected) for d in retrieved[:k]) else 0.0


def reciprocal_rank(retrieved: Sequence[str], expected: Sequence[str]) -> float:
    wanted = set(expected)
    for rank, doc in enumerate(retrieved, 1):
        if doc in wanted:
            return 1.0 / rank
    return 0.0


def precision_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    wanted = set(expected)
    return sum(1 for d in top if d in wanted) / len(top)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
