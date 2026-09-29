"""Document and chunk listing contracts."""

from __future__ import annotations

from pydantic import BaseModel


class ChunkView(BaseModel):
    chunk_id: str
    chunk_index: int
    document_version: int
    heading_path: str
    section: str | None
    subsection: str | None
    anchor: str | None
    content: str
    token_count: int
    has_code: bool
    is_split_oversize: bool
    embedding_model: str | None
    content_hash: str


class ChunkPage(BaseModel):
    document_id: str
    version: int | None
    total: int
    offset: int
    limit: int
    items: list[ChunkView]
