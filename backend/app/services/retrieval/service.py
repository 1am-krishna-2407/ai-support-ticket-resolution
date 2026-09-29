"""Hybrid retrieval: vector + lexical (+ exact phrase) candidates, RRF fusion, threshold.

Threshold semantics (ADR D8): RRF scores only order results. Relevance is judged on a
comparable score, the reranker score when a reranker is enabled, otherwise the cosine
similarity between the query and the chunk (computed for every candidate, including
lexical-only hits). A chunk that contains an exact error string or quoted phrase from the
query counts as evidence even when its cosine similarity is low, because exact error
strings are strong evidence that embeddings often miss.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import EmbeddingProviderError, RetrievalUnavailableError
from app.providers.embeddings import EmbeddingProvider
from app.repositories import chunks as chunk_repo
from app.repositories.chunks import ChunkHit
from app.schemas.retrieval import (
    ListRanks,
    RetrievalDiagnostics,
    RetrievalMethod,
    RetrievalResult,
    RetrievedChunk,
    ScoreBreakdown,
)
from app.services.retrieval.fusion import order_by_fused_score, reciprocal_rank_fusion
from app.services.retrieval.query import TransformedQuery
from app.services.retrieval.reranker import NoopReranker, Reranker


def _merge(existing: ChunkHit | None, new: ChunkHit) -> ChunkHit:
    if existing is None:
        return new
    return replace(
        existing,
        semantic_score=max(existing.semantic_score, new.semantic_score),
        lexical_score=max(existing.lexical_score, new.lexical_score),
        phrase_match=existing.phrase_match or new.phrase_match,
    )


def _url(hit: ChunkHit) -> str:
    return f"{hit.canonical_url}#{hit.anchor}" if hit.anchor else hit.canonical_url


def select_diverse(ordered: Sequence[ChunkHit], *, max_per_document: int) -> list[ChunkHit]:
    """Drop identical content (same hash or same normalised text) and cap chunks per document."""
    seen_hashes: set[str] = set()
    seen_text: set[str] = set()
    per_doc: dict[str, int] = {}
    selected: list[ChunkHit] = []
    for hit in ordered:
        normalized = " ".join(hit.content.split()).lower()
        if hit.content_hash in seen_hashes or normalized in seen_text:
            continue
        if per_doc.get(hit.document_id, 0) >= max_per_document:
            continue
        seen_hashes.add(hit.content_hash)
        seen_text.add(normalized)
        per_doc[hit.document_id] = per_doc.get(hit.document_id, 0) + 1
        selected.append(hit)
    return selected


class RetrievalService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: EmbeddingProvider | None,
        reranker: Reranker | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._embedder = embedder
        self._reranker = reranker or NoopReranker()

    async def retrieve(
        self,
        query: TransformedQuery,
        *,
        method: RetrievalMethod,
        top_k: int | None = None,
        categories: Sequence[str] | None = None,
    ) -> RetrievalResult:
        s = self._settings
        top_k = top_k or s.retrieval_top_k
        candidate_k = max(s.retrieval_candidate_k, top_k)
        timings: dict[str, float] = {}

        embedding: list[float] | None = None
        warnings: list[str] = []
        if self._embedder is None:
            if method is RetrievalMethod.VECTOR:
                raise EmbeddingProviderError("The embedding provider is not configured.")
            warnings.append(
                "Embeddings are not configured; used full-text and exact-phrase search."
            )
        else:
            started = time.perf_counter()
            try:
                embedding = await self._embedder.embed_query(query.retrieval_query)
            except EmbeddingProviderError as exc:
                if method is RetrievalMethod.VECTOR:
                    raise
                warnings.append(
                    f"Embeddings unavailable ({exc.message}); "
                    "used full-text and exact-phrase search."
                )
            timings["embedding"] = _ms(started)
        degraded = embedding is None

        semantic: list[ChunkHit] = []
        lexical: list[ChunkHit] = []
        phrase: list[ChunkHit] = []
        try:
            async with self._sessions() as session:
                if method is not RetrievalMethod.LEXICAL and embedding is not None:
                    started = time.perf_counter()
                    semantic = await chunk_repo.semantic_search(
                        session,
                        embedding,
                        limit=candidate_k,
                        query_text=query.retrieval_query,
                        categories=categories,
                    )
                    timings["semantic_search"] = _ms(started)
                if method is not RetrievalMethod.VECTOR:
                    started = time.perf_counter()
                    lexical = await chunk_repo.lexical_search(
                        session,
                        query.retrieval_query,
                        limit=candidate_k,
                        embedding=embedding,
                        categories=categories,
                    )
                    phrase = await chunk_repo.phrase_search(
                        session,
                        query.phrases,
                        limit=candidate_k,
                        embedding=embedding,
                        categories=categories,
                    )
                    timings["lexical_search"] = _ms(started)
        except SQLAlchemyError as exc:
            raise RetrievalUnavailableError() from exc

        started = time.perf_counter()
        hits: dict[str, ChunkHit] = {}
        for hit in (*semantic, *lexical, *phrase):
            hits[hit.chunk_id] = _merge(hits.get(hit.chunk_id), hit)
        lists = {
            "semantic": [h.chunk_id for h in semantic],
            "lexical": [h.chunk_id for h in lexical],
            "phrase": [h.chunk_id for h in phrase],
        }
        fused = reciprocal_rank_fusion(
            lists,
            k=s.retrieval_rrf_k,
            weights={
                "semantic": s.retrieval_semantic_weight,
                "lexical": s.retrieval_lexical_weight,
                "phrase": s.retrieval_lexical_weight,
            },
        )
        order = order_by_fused_score(fused, {cid: h.semantic_score for cid, h in hits.items()})
        diverse = select_diverse(
            [hits[cid] for cid in order], max_per_document=s.retrieval_max_chunks_per_document
        )
        timings["fusion"] = _ms(started)

        pool = diverse[: max(top_k * 3, top_k)]
        started = time.perf_counter()
        rerank_scores = await self._reranker.rerank(query.retrieval_query, pool)
        if rerank_scores is not None:
            timings["rerank"] = _ms(started)
            by_id = dict(zip((h.chunk_id for h in pool), rerank_scores, strict=True))
            pool = sorted(pool, key=lambda h: (-by_id[h.chunk_id], -fused[h.chunk_id], h.chunk_id))
        else:
            by_id = {}

        comparable = embedding is not None or rerank_scores is not None
        threshold = s.retrieval_relevance_threshold
        ranks = {name: {cid: i for i, cid in enumerate(ids, 1)} for name, ids in lists.items()}
        results: list[RetrievedChunk] = []
        for hit in pool:
            rerank = by_id.get(hit.chunk_id)
            final = (
                rerank
                if rerank is not None
                else (hit.semantic_score if embedding is not None else hit.lexical_score)
            )
            # Without comparable scores only an exact phrase match is trustworthy evidence.
            passes = hit.phrase_match or (comparable and final >= threshold)
            results.append(
                RetrievedChunk(
                    source_id="SRC-000",  # assigned below, after filtering
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    document_version=hit.document_version,
                    title=hit.title,
                    section=hit.section,
                    subsection=hit.subsection,
                    heading_path=hit.heading_path,
                    url=_url(hit),
                    category=hit.category,
                    content=hit.content,
                    token_count=hit.token_count,
                    exact_phrase_match=hit.phrase_match,
                    passes_threshold=passes,
                    scores=ScoreBreakdown(
                        semantic=round(hit.semantic_score, 6),
                        lexical=round(hit.lexical_score, 6),
                        fused=round(fused.get(hit.chunk_id, 0.0), 6),
                        rerank=round(rerank, 6) if rerank is not None else None,
                        final=round(final, 6),
                    ),
                    ranks=ListRanks(
                        semantic=ranks["semantic"].get(hit.chunk_id),
                        lexical=ranks["lexical"].get(hit.chunk_id),
                        phrase=ranks["phrase"].get(hit.chunk_id),
                    ),
                )
            )

        # Relevant results first (in fused/rerank order), then below-threshold ones for
        # inspection; only `passes_threshold` results may be used as evidence.
        results = [r for r in results if r.passes_threshold] + [
            r for r in results if not r.passes_threshold
        ]
        results = results[:top_k]
        for index, result in enumerate(results, 1):
            result.source_id = f"SRC-{index:03d}"

        passing = [r for r in results if r.passes_threshold]
        best = max((r.scores.final for r in results), default=None)
        basis = "rerank_score" if rerank_scores is not None else "semantic_similarity"
        if not comparable and passing:
            reason = f"{len(passing)} exact phrase match(es); similarity scores unavailable."
        elif not comparable:
            reason = "Similarity scores unavailable and no exact phrase matched the query."
        elif passing:
            reason = (
                f"{len(passing)} result(s) meet the threshold {threshold:.2f} on {basis}"
                f" (best {best:.3f})."
            )
        elif best is None:
            reason = "No matching documentation was found."
        else:
            reason = f"Best {basis} {best:.3f} is below the threshold {threshold:.2f}."

        return RetrievalResult(
            query=query.normalized,
            retrieval_query=query.retrieval_query,
            extracted_terms=query.terms,
            phrases=list(query.phrases),
            method=method,
            top_k=top_k,
            threshold=threshold,
            threshold_basis=basis,
            evidence_sufficient=bool(passing),
            reason=reason,
            degraded=degraded,
            warnings=warnings,
            results=results,
            diagnostics=RetrievalDiagnostics(
                candidates={k: len(v) for k, v in lists.items()},
                after_dedupe=len(diverse),
                timings_ms=timings,
                embedding_spec=self._embedder.spec if self._embedder else None,
                reranker=self._reranker.name,
            ),
        )


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
