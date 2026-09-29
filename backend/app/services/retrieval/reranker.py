"""Reranker protocol. The system works without a reranker (the default)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.repositories.chunks import ChunkHit


class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, candidates: Sequence[ChunkHit]) -> list[float] | None:
        """Relevance scores in [0, 1] aligned with `candidates`, or None to keep fused order."""
        ...


class NoopReranker:
    name = "none"

    async def rerank(self, query: str, candidates: Sequence[ChunkHit]) -> list[float] | None:
        return None
