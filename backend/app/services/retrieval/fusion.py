"""Reciprocal Rank Fusion (RRF).

    rrf(d) = Σ_i  w_i / (k + rank_i(d))       (rank is 1-based; absent lists contribute 0)

RRF scores depend only on ranks, so they are not comparable across queries and are
never used for thresholding (ADR D8); they only order candidates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, Sequence[str]],
    *,
    k: int,
    weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[str, float] = {}
    for name, ids in ranked_lists.items():
        weight = (weights or {}).get(name, 1.0)
        if weight <= 0:
            continue
        seen: set[str] = set()
        rank = 0
        for item in ids:
            if item in seen:
                continue  # a duplicate in one list keeps its best rank only
            seen.add(item)
            rank += 1
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
    return scores


def order_by_fused_score(fused: Mapping[str, float], tie_break: Mapping[str, float]) -> list[str]:
    """Descending fused score; ties broken by higher `tie_break` score, then by id."""
    return sorted(fused, key=lambda item: (-fused[item], -tie_break.get(item, 0.0), item))
