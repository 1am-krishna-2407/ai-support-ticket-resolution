from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import SettingsDep
from app.api.responses import BODY, errors
from app.api.routes.retrieval import RetrievalDep
from app.core.rate_limit import rate_limit
from app.core.tokenizer import get_tokenizer
from app.schemas.analysis import ClassifyResponse, TicketAnalysisResponse
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
    responses=BODY | errors(429),
    dependencies=[Depends(rate_limit("analyze"))],
    response_model=TicketAnalysisResponse,
    summary="Draft a grounded, cited resolution for a ticket (AI-generated draft)",
)
async def analyze_ticket(
    body: TicketInput, service: AnalysisDep, request: Request
) -> TicketAnalysisResponse:
    result = await service.analyze(body)
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
    responses=BODY | errors(429),
    dependencies=[Depends(rate_limit("analyze"))],
    response_model=ClassifyResponse,
    summary="Classify a ticket and estimate its priority (AI-assisted)",
)
async def classify_ticket(
    body: TicketInput, service: AnalysisDep, request: Request
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
