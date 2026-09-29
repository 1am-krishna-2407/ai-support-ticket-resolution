from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep
from app.core.errors import DocumentNotFoundError
from app.db.models import Document
from app.repositories import chunks as chunk_repo
from app.schemas.documents import ChunkPage, ChunkView
from app.schemas.retrieval import RetrievalResult, SearchRequest
from app.schemas.tickets import TicketRetrieveRequest
from app.services.retrieval.query import transform_query
from app.services.retrieval.service import RetrievalService

router = APIRouter(tags=["retrieval"])


def get_retrieval_service(request: Request, settings: SettingsDep) -> RetrievalService:
    return RetrievalService(
        settings=settings,
        session_factory=request.app.state.session_factory,
        embedder=request.app.state.embedder,
        reranker=request.app.state.reranker,
    )


RetrievalDep = Annotated[RetrievalService, Depends(get_retrieval_service)]


@router.post("/search", response_model=RetrievalResult, summary="Search the knowledge base")
async def search(
    body: SearchRequest, service: RetrievalDep, settings: SettingsDep
) -> RetrievalResult:
    query = transform_query(body.query, max_chars=settings.retrieval_max_query_chars)
    return await service.retrieve(
        query,
        method=body.method,
        top_k=body.top_k,
        categories=[c.value for c in body.categories] if body.categories else None,
    )


@router.post(
    "/tickets/retrieve",
    response_model=RetrievalResult,
    summary="Retrieve evidence for a support ticket",
)
async def retrieve_for_ticket(
    body: TicketRetrieveRequest, service: RetrievalDep, settings: SettingsDep
) -> RetrievalResult:
    query = transform_query(
        body.title, body.description, max_chars=settings.retrieval_max_query_chars
    )
    return await service.retrieve(query, method=body.method, top_k=body.top_k)


@router.get(
    "/documents/{document_id}/chunks",
    response_model=ChunkPage,
    summary="List a document's chunks (active version unless `version` is given)",
)
async def document_chunks(
    document_id: str,
    session: SessionDep,
    version: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ChunkPage:
    exists = await session.scalar(
        select(Document.id).where(Document.document_id == document_id).limit(1)
    )
    if exists is None:
        raise DocumentNotFoundError(details={"document_id": document_id})
    total, rows = await chunk_repo.chunks_for_document(
        session, document_id, version=version, offset=offset, limit=limit
    )
    return ChunkPage(
        document_id=document_id,
        version=version,
        total=total,
        offset=offset,
        limit=limit,
        items=[ChunkView.model_validate(row) for row in rows],
    )
