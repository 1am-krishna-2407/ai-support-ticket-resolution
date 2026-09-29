"""Document, chunk, and ingestion-job contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

ATTRIBUTION = (
    "Content from GitLab Docs (https://docs.gitlab.com), licensed under CC BY-SA 4.0 "
    "(https://creativecommons.org/licenses/by-sa/4.0/). This project is not affiliated "
    "with or endorsed by GitLab Inc."
)


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


class DocumentSummary(BaseModel):
    document_id: str
    title: str
    canonical_url: str
    category: str
    source_type: str
    license: str
    version: int
    status: str
    enabled: bool
    content_hash: str
    retrieved_at: datetime
    published_at: datetime | None
    chunk_count: int


class DocumentPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[DocumentSummary]
    attribution: str = ATTRIBUTION


class DocumentVersion(BaseModel):
    version: int
    status: str
    content_hash: str
    retrieved_at: datetime
    published_at: datetime | None
    chunk_count: int
    embedding_model: str | None
    ingestion_job_id: uuid.UUID | None


class DocumentDetail(DocumentSummary):
    source_version: str | None
    embedding_model: str | None
    versions: list[DocumentVersion]
    attribution: str = ATTRIBUTION


class IngestionRunRequest(BaseModel):
    force: bool = False
    only: list[str] = Field(default_factory=list, max_length=300)


class IngestionJobView(BaseModel):
    id: uuid.UUID
    status: str
    trigger: str
    config: dict[str, Any]
    total_documents: int
    processed_documents: int
    changed_documents: int
    failed_documents: int
    total_chunks: int
    embedding_calls: int
    reused_embeddings: int
    error_summary: list[dict[str, Any]]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class IngestionStatus(BaseModel):
    running: bool
    latest: IngestionJobView | None
