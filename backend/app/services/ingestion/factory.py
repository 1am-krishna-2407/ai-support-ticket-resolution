"""Builds an IngestionService from settings (shared by the CLI and the API)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.tokenizer import get_tokenizer
from app.providers.embeddings import EmbeddingProvider
from app.services.ingestion.fetcher import DocumentFetcher
from app.services.ingestion.parser import GitLabDocsParser
from app.services.ingestion.raw_store import RawStore
from app.services.ingestion.service import IngestionService


def build_ingestion_service(
    settings: Settings,
    *,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    fetcher: DocumentFetcher,
    embedder: EmbeddingProvider,
) -> IngestionService:
    return IngestionService(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        fetcher=fetcher,
        parser=GitLabDocsParser(),
        embedder=embedder,
        count_tokens=get_tokenizer(settings.tokenizer_encoding).count,
        raw_store=RawStore(Path(settings.raw_storage_dir)),
        manifest_path=Path(settings.manifest_path),
    )
