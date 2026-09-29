"""Run the retrieval golden queries and sweep relevance thresholds.

    python -m app.scripts.golden_retrieval [--path evaluation/golden_queries.json] [--k 5]

Prints JSON: per-method Recall@K, HitRate@K, and MRR over answerable queries, plus a
threshold sweep showing, for each candidate threshold, the share of out-of-scope
queries correctly refused and of answerable queries wrongly refused. Retrieval runs
with the threshold at 0 so the raw scores can be re-evaluated at every candidate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.providers.embeddings import create_embedding_provider
from app.schemas.retrieval import RetrievalMethod, RetrievalResult
from app.scripts._offline import offline_settings
from app.services.evaluation.retrieval_metrics import (
    hit_rate_at_k,
    mean,
    recall_at_k,
    reciprocal_rank,
)
from app.services.retrieval.query import transform_query
from app.services.retrieval.service import RetrievalService

THRESHOLDS = [round(0.30 + 0.025 * i, 3) for i in range(17)]  # 0.300 … 0.700


def _documents(result: RetrievalResult) -> list[str]:
    seen: list[str] = []
    for r in result.results:
        if r.document_id not in seen:
            seen.append(r.document_id)
    return seen


def _sufficient(result: RetrievalResult, threshold: float) -> bool:
    return any(r.exact_phrase_match or r.scores.semantic >= threshold for r in result.results)


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="evaluation/golden_queries.json")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)

    settings = offline_settings(get_settings())
    configure_logging("WARNING", sys.stderr)  # keep stdout for the JSON report
    probe = settings.model_copy(update={"retrieval_relevance_threshold": 0.0})
    dataset = json.loads(await asyncio.to_thread(Path(args.path).read_text, encoding="utf-8"))
    queries = dataset["queries"]

    engine = create_engine(settings)
    embedder = create_embedding_provider(settings)
    service = RetrievalService(
        settings=probe, session_factory=create_session_factory(engine), embedder=embedder
    )
    report: dict[str, Any] = {"dataset_version": dataset["version"], "k": args.k, "methods": {}}
    per_query: dict[str, dict[str, RetrievalResult]] = {}
    try:
        for method in (RetrievalMethod.VECTOR, RetrievalMethod.LEXICAL, RetrievalMethod.HYBRID):
            recalls, hits, rrs, misses = [], [], [], []
            for q in queries:
                tq = transform_query(q["query"], max_chars=settings.retrieval_max_query_chars)
                result = await service.retrieve(tq, method=method, top_k=max(args.k, 8))
                per_query.setdefault(q["id"], {})[method.value] = result
                expected = q["expected_documents"]
                if not expected:
                    continue
                docs = _documents(result)
                recalls.append(recall_at_k(docs, expected, args.k))
                hits.append(hit_rate_at_k(docs, expected, args.k))
                rrs.append(reciprocal_rank(docs, expected))
                if not hits[-1]:
                    misses.append({"id": q["id"], "top_documents": docs[: args.k]})
            report["methods"][method.value] = {
                f"recall@{args.k}": round(mean(recalls), 3),
                f"hit_rate@{args.k}": round(mean(hits), 3),
                "mrr": round(mean(rrs), 3),
                "misses": misses,
            }

        positives = [q for q in queries if q["expected_documents"]]
        negatives = [q for q in queries if not q["expected_documents"]]
        best_relevant = {
            q["id"]: max(
                (
                    r.scores.semantic
                    for r in per_query[q["id"]]["hybrid"].results
                    if r.document_id in q["expected_documents"]
                ),
                default=0.0,
            )
            for q in positives
        }
        best_negative = {
            q["id"]: max(
                (r.scores.semantic for r in per_query[q["id"]]["hybrid"].results), default=0.0
            )
            for q in negatives
        }
        report["hybrid_scores"] = {
            "best_relevant_similarity": {k: round(v, 3) for k, v in best_relevant.items()},
            "best_out_of_scope_similarity": {k: round(v, 3) for k, v in best_negative.items()},
        }
        sweep = []
        for t in THRESHOLDS:
            correct_refusals = mean(
                [0.0 if _sufficient(per_query[q["id"]]["hybrid"], t) else 1.0 for q in negatives]
            )
            false_refusals = mean(
                [0.0 if _sufficient(per_query[q["id"]]["hybrid"], t) else 1.0 for q in positives]
            )
            sweep.append(
                {
                    "threshold": t,
                    "correct_refusal_rate": round(correct_refusals, 3),
                    "false_refusal_rate": round(false_refusals, 3),
                }
            )
        report["threshold_sweep"] = sweep
    finally:
        await embedder.aclose()
        await engine.dispose()
    print(json.dumps(report, indent=2))
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
