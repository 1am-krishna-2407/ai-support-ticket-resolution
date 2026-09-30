"""Run knowledge ingestion.

    python -m app.scripts.ingest [--force] [--only DOCUMENT_ID ...] [--fail-fast]

Prints a JSON summary and exits non-zero if the job failed. Paths in settings
(MANIFEST_PATH, RAW_STORAGE_DIR) are relative to the working directory, which
is /app inside the backend container.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.tokenizer import get_tokenizer
from app.db.session import create_engine, create_session_factory
from app.providers.embeddings import create_embedding_provider
from app.services.ingestion.fetcher import HttpDocumentFetcher
from app.services.ingestion.manifest import ManifestError
from app.services.ingestion.parser import GitLabDocsParser
from app.services.ingestion.raw_store import RawStore
from app.services.ingestion.service import IngestionOptions, IngestionService


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest the documentation knowledge base.")
    parser.add_argument("--force", action="store_true", help="re-process unchanged documents")
    parser.add_argument("--only", nargs="+", default=[], metavar="DOCUMENT_ID")
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first failure")
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, sys.stderr)  # keep stdout for the JSON summary
    engine = create_engine(settings)
    fetcher = HttpDocumentFetcher(settings)
    try:
        embedder = create_embedding_provider(settings)
    except AppError as exc:
        await fetcher.aclose()
        await engine.dispose()
        print(json.dumps({"status": "failed", "error": exc.message}), file=sys.stderr)
        return 2
    try:
        service = IngestionService(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            fetcher=fetcher,
            parser=GitLabDocsParser(),
            embedder=embedder,
            count_tokens=get_tokenizer(settings.tokenizer_encoding).count,
            raw_store=RawStore(Path(settings.raw_storage_dir)),
            manifest_path=Path(settings.manifest_path),
        )
        summary = await service.run(
            IngestionOptions(
                force=args.force, only=tuple(args.only), fail_fast=args.fail_fast, trigger="cli"
            )
        )
    except (AppError, ManifestError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2
    finally:
        await fetcher.aclose()
        await embedder.aclose()
        await engine.dispose()
    print(json.dumps(summary.to_dict(), indent=2, default=str))
    return 0 if summary.status == "completed" else 1


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
