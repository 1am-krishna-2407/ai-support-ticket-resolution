from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.core.categories import Category
from app.core.errors import DocumentNotFoundError
from app.db.models import Document, DocumentStatus
from app.repositories import documents as doc_repo
from app.schemas.documents import (
    DocumentDetail,
    DocumentPage,
    DocumentSummary,
    DocumentVersion,
)

router = APIRouter(tags=["documents"])


def _summary(doc: Document, chunk_count: int) -> DocumentSummary:
    return DocumentSummary(
        document_id=doc.document_id,
        title=doc.title,
        canonical_url=doc.canonical_url,
        category=doc.category,
        source_type=doc.source_type,
        license=doc.license,
        version=doc.version,
        status=doc.status,
        enabled=doc.enabled,
        content_hash=doc.content_hash,
        retrieved_at=doc.retrieved_at,
        published_at=doc.published_at,
        chunk_count=chunk_count,
    )


@router.get("/documents", response_model=DocumentPage, summary="List knowledge-base documents")
async def list_documents(
    session: SessionDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    category: Category | None = None,
    status: Annotated[
        DocumentStatus, Query(description="Version status to list (default: active).")
    ] = DocumentStatus.ACTIVE,
    all_statuses: Annotated[
        bool, Query(description="List every stored version, ignoring `status`.")
    ] = False,
    enabled: bool | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> DocumentPage:
    filters = doc_repo.DocumentFilters(
        search=search or None,
        category=category.value if category else None,
        status=None if all_statuses else status.value,
        enabled=enabled,
    )
    total, rows = await doc_repo.list_documents(session, filters, offset=offset, limit=limit)
    return DocumentPage(
        total=total,
        offset=offset,
        limit=limit,
        items=[_summary(doc, count) for doc, count in rows],
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="A document's current state and version history",
)
async def get_document(document_id: str, session: SessionDep) -> DocumentDetail:
    versions = await doc_repo.document_versions(session, document_id)
    if not versions:
        raise DocumentNotFoundError(details={"document_id": document_id})
    active = next((v for v in versions if v[0].status == DocumentStatus.ACTIVE.value), versions[0])
    doc, count, model = active
    return DocumentDetail(
        **_summary(doc, count).model_dump(),
        source_version=doc.source_version,
        embedding_model=model,
        versions=[
            DocumentVersion(
                version=v.version,
                status=v.status,
                content_hash=v.content_hash,
                retrieved_at=v.retrieved_at,
                published_at=v.published_at,
                chunk_count=c,
                embedding_model=m,
                ingestion_job_id=v.ingestion_job_id,
            )
            for v, c, m in versions
        ],
    )
