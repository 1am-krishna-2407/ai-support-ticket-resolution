"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, BodySizeLimitMiddleware, RequestContextMiddleware
from app.db.session import create_engine, create_session_factory
from app.providers.embeddings import EmbeddingProvider, create_embedding_provider
from app.providers.llm import LLMProvider, OpenAICompatibleChatProvider
from app.services.analysis.prompts import PromptRegistry
from app.services.ingestion.fetcher import HttpDocumentFetcher
from app.services.retrieval.reranker import NoopReranker

logger = logging.getLogger("app.main")


def build_embedder(settings: Settings) -> EmbeddingProvider | None:
    """The embedding provider, or None when no key is configured (lexical search still works)."""
    key = settings.resolved_embedding_api_key
    if key is None or not key.get_secret_value():
        logger.warning(
            "no embedding credentials configured: search falls back to full-text and exact-phrase",
            extra={"embedding_provider": settings.embedding_provider.value},
        )
        return None
    return create_embedding_provider(settings)


def build_llm(settings: Settings) -> LLMProvider | None:
    """The LLM provider, or None when no key is configured (analysis degrades to review)."""
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        logger.warning("LLM_API_KEY not set: ticket analysis will return review-only drafts")
        return None
    return OpenAICompatibleChatProvider(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.embedder = build_embedder(settings)
        app.state.reranker = NoopReranker()
        app.state.llm = build_llm(settings)
        app.state.prompts = PromptRegistry()
        app.state.ingestion_start_lock = asyncio.Lock()
        app.state.fetcher_factory = HttpDocumentFetcher
        app.state.ingestion_task = None
        app.state.evaluation_start_lock = asyncio.Lock()
        app.state.evaluation_task = None
        try:
            yield
        finally:
            for task in (app.state.ingestion_task, app.state.evaluation_task):
                if task is not None and not task.done():
                    # Rows keep their stage; the next run's recovery marks them failed.
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if app.state.embedder is not None:
                await app.state.embedder.aclose()
            if app.state.llm is not None:
                await app.state.llm.aclose()
            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    # Order: added last runs first. RequestContext must wrap everything so that
    # every response (including 413s and CORS preflights) carries a request ID.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Admin-Token", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app
