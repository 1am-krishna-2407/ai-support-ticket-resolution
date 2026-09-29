"""Search queries over active, enabled document chunks.

SQL is assembled from module-level constant fragments only; every value derived from
user input (query text, phrases, categories, embeddings, limits) is a bound parameter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    document_id: str
    document_version: int
    chunk_index: int
    title: str
    section: str | None
    subsection: str | None
    heading_path: str
    content: str
    content_hash: str
    category: str
    canonical_url: str
    anchor: str | None
    token_count: int
    semantic_score: float  # cosine similarity to the query embedding
    lexical_score: float  # ts_rank_cd normalised to [0, 1)
    phrase_match: bool = False


_COLUMNS = """
    c.chunk_id, c.document_id, c.document_version, c.chunk_index, c.title, c.section,
    c.subsection, c.heading_path, c.content, c.content_hash, c.category, c.canonical_url,
    c.anchor, c.token_count
"""
_ACTIVE = "d.status = 'active' AND d.enabled"

# OR-query over the lexemes of the input: websearch_to_tsquery ANDs every word, which
# almost never matches a multi-sentence ticket. Lexemes are quoted so punctuation in
# identifiers cannot break tsquery syntax.
_OR_TSQUERY = """
    (SELECT to_tsquery('simple', coalesce(
        nullif(array_to_string(ARRAY(
            SELECT quote_literal(lexeme)
            FROM unnest(tsvector_to_array(to_tsvector('english', :q))) AS lexeme
        ), ' | '), ''), 'x_no_terms_x')))
"""


def _hit(row: object, phrase_match: bool = False) -> ChunkHit:
    m = row._mapping  # type: ignore[attr-defined]
    return ChunkHit(
        chunk_id=m["chunk_id"],
        document_id=m["document_id"],
        document_version=m["document_version"],
        chunk_index=m["chunk_index"],
        title=m["title"],
        section=m["section"],
        subsection=m["subsection"],
        heading_path=m["heading_path"],
        content=m["content"],
        content_hash=m["content_hash"],
        category=m["category"],
        canonical_url=m["canonical_url"],
        anchor=m["anchor"],
        token_count=m["token_count"],
        semantic_score=float(m["semantic_score"]) if m["semantic_score"] is not None else 0.0,
        lexical_score=float(m["lexical_score"]) if m["lexical_score"] is not None else 0.0,
        phrase_match=phrase_match,
    )


def _category_filter(categories: Sequence[str] | None) -> str:
    return " AND c.category = ANY(:categories)" if categories else ""


_TEXT_ARRAY = ARRAY(String)


def _bind(stmt, params: dict[str, object]):  # type: ignore[no-untyped-def]
    """Attach explicit types for vector and text[] parameters (asyncpg cannot infer them)."""
    binds = []
    if "embedding" in params:
        binds.append(bindparam("embedding", type_=Vector()))
    for name in ("phrases", "categories"):
        if name in params:
            binds.append(bindparam(name, type_=_TEXT_ARRAY))
    return stmt.bindparams(*binds) if binds else stmt


async def semantic_search(
    session: AsyncSession,
    embedding: Sequence[float],
    *,
    limit: int,
    query_text: str,
    categories: Sequence[str] | None = None,
) -> list[ChunkHit]:
    # Iterative HNSW scans keep returning rows after the active/enabled filter removes some.
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {max(40, min(limit * 2, 1000))}"))
    stmt = text(
        f"""
        SELECT {_COLUMNS},
               1 - (c.embedding <=> :embedding) AS semantic_score,
               ts_rank_cd(c.search_vector, {_OR_TSQUERY}, 32) AS lexical_score
        FROM document_chunks c JOIN documents d ON d.id = c.document_pk
        WHERE {_ACTIVE} AND c.embedding IS NOT NULL{_category_filter(categories)}
        ORDER BY c.embedding <=> :embedding, c.chunk_id
        LIMIT :limit
        """  # noqa: S608 (only module constants are interpolated; user input is bound)
    )
    params: dict[str, object] = {"embedding": list(embedding), "limit": limit, "q": query_text}
    if categories:
        params["categories"] = list(categories)
    rows = await session.execute(_bind(stmt, params), params)
    return [_hit(r) for r in rows]


async def lexical_search(
    session: AsyncSession,
    query_text: str,
    *,
    limit: int,
    embedding: Sequence[float] | None,
    categories: Sequence[str] | None = None,
) -> list[ChunkHit]:
    semantic = "1 - (c.embedding <=> :embedding)" if embedding is not None else "NULL"
    stmt = text(
        f"""
        WITH q AS (SELECT {_OR_TSQUERY} AS query)
        SELECT {_COLUMNS},
               {semantic} AS semantic_score,
               ts_rank_cd(c.search_vector, q.query, 32) AS lexical_score
        FROM document_chunks c JOIN documents d ON d.id = c.document_pk, q
        WHERE {_ACTIVE} AND c.search_vector @@ q.query{_category_filter(categories)}
        ORDER BY lexical_score DESC, c.chunk_id
        LIMIT :limit
        """  # noqa: S608 (only module constants are interpolated; user input is bound)
    )
    params: dict[str, object] = {"q": query_text, "limit": limit}
    if embedding is not None:
        params["embedding"] = list(embedding)
    if categories:
        params["categories"] = list(categories)
    rows = await session.execute(_bind(stmt, params), params)
    return [_hit(r) for r in rows]


async def phrase_search(
    session: AsyncSession,
    phrases: Sequence[str],
    *,
    limit: int,
    embedding: Sequence[float] | None,
    categories: Sequence[str] | None = None,
) -> list[ChunkHit]:
    """Chunks containing any phrase verbatim (case-insensitive), e.g. exact error strings."""
    if not phrases:
        return []
    semantic = "1 - (c.embedding <=> :embedding)" if embedding is not None else "NULL"
    stmt = text(
        f"""
        SELECT {_COLUMNS},
               {semantic} AS semantic_score,
               (SELECT count(*) FROM unnest(:phrases) p
                 WHERE strpos(lower(c.content), lower(p)) > 0)::float
                 / cardinality(:phrases) AS lexical_score
        FROM document_chunks c JOIN documents d ON d.id = c.document_pk
        WHERE {_ACTIVE}
          AND EXISTS (SELECT 1 FROM unnest(:phrases) p WHERE strpos(lower(c.content), lower(p)) > 0)
          {_category_filter(categories)}
        ORDER BY lexical_score DESC, c.chunk_id
        LIMIT :limit
        """  # noqa: S608 (only module constants are interpolated; user input is bound)
    )
    params: dict[str, object] = {"phrases": list(phrases), "limit": limit}
    if embedding is not None:
        params["embedding"] = list(embedding)
    if categories:
        params["categories"] = list(categories)
    rows = await session.execute(_bind(stmt, params), params)
    return [_hit(r, phrase_match=True) for r in rows]


async def chunks_for_document(
    session: AsyncSession, document_id: str, *, version: int | None, offset: int, limit: int
) -> tuple[int, list[dict[str, object]]]:
    version_clause = "c.document_version = :version" if version is not None else _ACTIVE
    base = f"""
        FROM document_chunks c JOIN documents d ON d.id = c.document_pk
        WHERE c.document_id = :document_id AND {version_clause}
    """
    params: dict[str, object] = {"document_id": document_id}
    if version is not None:
        params["version"] = version
    total = int((await session.execute(text(f"SELECT count(*) {base}"), params)).scalar_one())
    rows = await session.execute(
        text(
            f"""SELECT c.chunk_id, c.chunk_index, c.document_version, c.heading_path, c.section,
                       c.subsection, c.anchor, c.content, c.token_count, c.has_code,
                       c.is_split_oversize, c.embedding_model, c.content_hash
                {base} ORDER BY c.chunk_index LIMIT :limit OFFSET :offset"""
        ),
        {**params, "limit": limit, "offset": offset},
    )
    return total, [dict(r._mapping) for r in rows]
