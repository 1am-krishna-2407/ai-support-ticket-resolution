"""Builds an EvaluationRunner from settings and providers (shared by the CLI and the API)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.tokenizer import get_tokenizer
from app.providers.embeddings import EmbeddingProvider
from app.providers.llm import LLMProvider
from app.services.analysis.prompts import PromptRegistry
from app.services.analysis.service import TicketAnalysisService
from app.services.evaluation.runner import EvaluationRunner
from app.services.retrieval.service import RetrievalService


def build_evaluation_runner(
    settings: Settings,
    *,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    embedder: EmbeddingProvider | None,
    llm: LLMProvider | None,
    prompts: PromptRegistry,
) -> EvaluationRunner:
    retrieval = RetrievalService(
        settings=settings, session_factory=session_factory, embedder=embedder
    )
    analysis = (
        TicketAnalysisService(
            settings=settings,
            llm=llm,
            retrieval=retrieval,
            prompts=prompts,
            count_tokens=get_tokenizer(settings.tokenizer_encoding).count,
            embedding_spec=embedder.spec if embedder else None,
        )
        if llm is not None
        else None
    )
    return EvaluationRunner(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        retrieval=retrieval,
        analysis=analysis,
        embedding_spec=embedder.spec if embedder else None,
        llm_model=llm.model if llm else None,
        prompt_versions=prompts.versions(),
    )
