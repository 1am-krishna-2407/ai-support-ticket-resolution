"""Retrieval request/response contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.core.categories import Category

QUERY_MAX_CHARS = 4000


class RetrievalMethod(StrEnum):
    VECTOR = "vector"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=QUERY_MAX_CHARS)
    method: RetrievalMethod = RetrievalMethod.HYBRID
    top_k: int | None = Field(default=None, ge=1, le=50)
    categories: list[Category] | None = Field(default=None, max_length=11)


class ScoreBreakdown(BaseModel):
    semantic: float = Field(description="Cosine similarity between query and chunk embeddings.")
    lexical: float = Field(description="Full-text rank (ts_rank_cd, normalised to [0, 1)).")
    fused: float = Field(description="Reciprocal Rank Fusion score; orders results only.")
    rerank: float | None = Field(description="Reranker score, when a reranker is enabled.")
    final: float = Field(description="Score compared with the relevance threshold.")


class ListRanks(BaseModel):
    semantic: int | None
    lexical: int | None
    phrase: int | None


class RetrievedChunk(BaseModel):
    source_id: str = Field(pattern=r"^SRC-\d{3}$")
    chunk_id: str
    document_id: str
    document_version: int
    title: str
    section: str | None
    subsection: str | None
    heading_path: str
    url: str = Field(description="Canonical URL, with the section anchor when available.")
    category: str
    content: str
    token_count: int
    exact_phrase_match: bool
    passes_threshold: bool
    scores: ScoreBreakdown
    ranks: ListRanks


class RetrievalDiagnostics(BaseModel):
    candidates: dict[str, int]
    after_dedupe: int
    timings_ms: dict[str, float]
    embedding_spec: str | None
    reranker: str


class RetrievalResult(BaseModel):
    query: str
    retrieval_query: str
    extracted_terms: dict[str, list[str]]
    phrases: list[str]
    method: RetrievalMethod
    top_k: int
    threshold: float
    threshold_basis: str
    evidence_sufficient: bool
    reason: str
    degraded: bool = Field(
        default=False,
        description="True when embeddings were unavailable and only full-text and exact-phrase "
        "matching was used; then only exact-phrase matches count as evidence.",
    )
    warnings: list[str] = Field(default_factory=list)
    results: list[RetrievedChunk]
    diagnostics: RetrievalDiagnostics
