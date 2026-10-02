from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import SessionDep, SettingsDep
from app.api.responses import BODY, READ, errors
from app.api.routes.retrieval import RetrievalDep
from app.core.auth import CurrentUser, is_admin
from app.core.errors import ForbiddenError, NotFoundError
from app.core.rate_limit import rate_limit
from app.core.tokenizer import get_tokenizer
from app.db.models import TicketAnalysisRecord, User
from app.repositories import ticket_history
from app.schemas.analysis import ClassifyResponse, TicketAnalysisResponse
from app.schemas.auth import TicketHistoryDetail, TicketHistoryItem, TicketHistoryPage
from app.schemas.tickets import TicketInput
from app.services.analysis.service import TicketAnalysisService

router = APIRouter(tags=["tickets"])


def get_analysis_service(
    request: Request, settings: SettingsDep, retrieval: RetrievalDep
) -> TicketAnalysisService:
    embedder = request.app.state.embedder
    return TicketAnalysisService(
        settings=settings,
        llm=request.app.state.llm,
        retrieval=retrieval,
        prompts=request.app.state.prompts,
        count_tokens=get_tokenizer(settings.tokenizer_encoding).count,
        embedding_spec=embedder.spec if embedder is not None else None,
    )


AnalysisDep = Annotated[TicketAnalysisService, Depends(get_analysis_service)]


@router.post(
    "/tickets/analyze",
    responses=BODY | errors(401, 429),
    dependencies=[Depends(rate_limit("analyze"))],
    response_model=TicketAnalysisResponse,
    summary="Draft a grounded, cited resolution for a ticket and save it to your history",
)
async def analyze_ticket(
    body: TicketInput,
    service: AnalysisDep,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> TicketAnalysisResponse:
    result = await service.analyze(body)
    record = await ticket_history.save(
        session,
        TicketAnalysisRecord(
            user_id=user.id,
            title=body.title,
            description=body.description,
            environment=body.environment.model_dump(exclude_none=True)
            if body.environment
            else None,
            status=result.resolution.status,
            category=result.classification.category,
            priority=result.priority.level,
            grounded=result.grounding.grounded,
            latency_ms=result.timings_ms.get("total"),
            result=result.model_dump(mode="json"),
        ),
    )
    await session.commit()
    result.analysis_id = record.id
    request.state.request_log = {
        "stage_timings": result.timings_ms,
        "outcome": {
            "status": result.resolution.status,
            "category": result.classification.category,
            "priority": result.priority.level,
            "grounded": result.grounding.grounded,
            "review_reasons": [str(r) for r in result.human_review.reasons],
            "sources": len(result.sources),
            "errors": [f"{e.stage}:{e.code}" for e in result.errors],
        },
    }
    return result


@router.post(
    "/tickets/classify",
    responses=BODY | errors(401, 429),
    dependencies=[Depends(rate_limit("analyze"))],
    response_model=ClassifyResponse,
    summary="Classify a ticket and estimate its priority (AI-assisted; not saved)",
)
async def classify_ticket(
    body: TicketInput, service: AnalysisDep, request: Request, _: CurrentUser
) -> ClassifyResponse:
    result = await service.classify(body)
    request.state.request_log = {
        "stage_timings": result.timings_ms,
        "outcome": {
            "category": result.classification.category,
            "priority": result.priority.level,
            "review_reasons": [str(r) for r in result.human_review.reasons],
            "errors": [f"{e.stage}:{e.code}" for e in result.errors],
        },
    }
    return result


@router.get(
    "/tickets/history",
    response_model=TicketHistoryPage,
    responses=READ | errors(401, 403),
    summary="Your saved analyses, newest first (admins: scope=all for every user)",
)
async def history(
    user: CurrentUser,
    session: SessionDep,
    scope: Literal["mine", "all"] = "mine",
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TicketHistoryPage:
    if scope == "all" and not is_admin(user):
        raise ForbiddenError("Only admins can list every user's analyses.")
    total, rows = await ticket_history.list_records(
        session, user_id=None if scope == "all" else user.id, offset=offset, limit=limit
    )
    items = [
        TicketHistoryItem(
            id=r.id,
            created_at=r.created_at,
            title=r.title,
            status=r.status,
            category=r.category,
            priority=r.priority,
            grounded=r.grounded,
            latency_ms=r.latency_ms,
            user_email=email,
        )
        for r, email in rows
    ]
    return TicketHistoryPage(total=total, offset=offset, limit=limit, scope=scope, items=items)


async def _owned(
    session: SessionDep, user: User, record_id: uuid.UUID
) -> tuple[TicketAnalysisRecord, str]:
    found = await ticket_history.get(session, record_id)
    # Someone else's analysis is reported as missing, not forbidden: IDs reveal nothing.
    if found is None or (found[0].user_id != user.id and not is_admin(user)):
        raise NotFoundError("Analysis not found.", details={"analysis_id": str(record_id)})
    return found


@router.get(
    "/tickets/history/{analysis_id}",
    response_model=TicketHistoryDetail,
    responses=READ | errors(401, 404),
    summary="One saved analysis: the ticket as submitted and the full result",
)
async def history_item(
    analysis_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> TicketHistoryDetail:
    record, email = await _owned(session, user, analysis_id)
    result = TicketAnalysisResponse.model_validate(record.result)
    result.analysis_id = record.id
    return TicketHistoryDetail(
        id=record.id,
        created_at=record.created_at,
        user_email=email,
        title=record.title,
        description=record.description,
        environment=record.environment,
        result=result,
    )


@router.delete(
    "/tickets/history/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=errors(401, 404, 422),
    summary="Delete a saved analysis (yours, or any for admins)",
)
async def delete_history_item(
    analysis_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Response:
    await _owned(session, user, analysis_id)
    await ticket_history.delete_record(session, analysis_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
