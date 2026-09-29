from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.db.base import Base
from tests.integration.conftest import TEST_EMBEDDING_DIMENSION

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "documents",
    "document_chunks",
    "ingestion_jobs",
    "evaluation_runs",
    "evaluation_cases",
    "request_logs",
}
EXPECTED_INDEXES = {
    "ix_document_chunks_embedding_hnsw",
    "ix_document_chunks_search_vector",
    "ix_document_chunks_metadata",
    "ix_document_chunks_doc_version",
    "ix_documents_active_enabled",
    "uq_documents_one_active_version",
    "ix_documents_document_id",
    "ix_documents_category",
    "ix_documents_status",
    "ix_documents_enabled",
}


def _query(url: str, fn: Callable[[Connection], Any]) -> Any:
    async def _run() -> Any:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            result = await conn.run_sync(fn)
        await engine.dispose()
        return result

    return asyncio.run(_run())


def _scalars(sql: str) -> Callable[[Connection], set[Any]]:
    return lambda conn: {row[0] for row in conn.exec_driver_sql(sql)}


def _url(cfg: Config) -> str:
    return str(cfg.attributes["database_url"])


def test_upgrade_creates_extension_tables_and_indexes(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    url = _url(alembic_config)

    assert "vector" in _query(url, _scalars("SELECT extname FROM pg_extension"))
    tables = _query(url, _scalars("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    assert EXPECTED_TABLES <= tables
    indexes = _query(url, _scalars("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"))
    assert EXPECTED_INDEXES <= indexes

    hnsw = _query(
        url,
        _scalars(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_document_chunks_embedding_hnsw'"
        ),
    )
    assert any("hnsw" in d and "vector_cosine_ops" in d for d in hnsw)

    dims = _query(
        url,
        _scalars(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
        ),
    )
    assert dims == {TEST_EMBEDDING_DIMENSION}


def test_search_vector_is_generated_and_weighted(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    def _insert_and_read(conn: Connection) -> tuple[str, int]:
        conn.exec_driver_sql(
            "INSERT INTO documents (id, document_id, version, title, canonical_url, category,"
            " source_type, license, content_hash, retrieved_at, status)"
            " VALUES (gen_random_uuid(), 'doc-1', 1, 'T', 'https://docs.gitlab.com/x', 'ci_cd',"
            " 'documentation', 'CC BY-SA 4.0', 'h', now(), 'active')"
        )
        conn.exec_driver_sql(
            "INSERT INTO document_chunks (id, chunk_id, document_pk, document_id,"
            " document_version, chunk_index, title, heading_path, content, content_hash,"
            " token_count, category, canonical_url, has_code, is_split_oversize, embedding)"
            " SELECT gen_random_uuid(), 'c1', id, 'doc-1', 1, 0, 'T', 'Runners > Registration',"
            " 'Use gitlab-runner register with a token', 'h', 7, 'ci_cd',"
            " 'https://docs.gitlab.com/x', false, false, '[1,0,0,0,0,0,0,0]' FROM documents"
        )
        row = conn.exec_driver_sql(
            "SELECT search_vector::text, "
            " (SELECT count(*) FROM document_chunks"
            "   WHERE search_vector @@ websearch_to_tsquery('english', 'runner register'))"
            " FROM document_chunks"
        ).one()
        conn.rollback()
        return str(row[0]), int(row[1])

    vector_text, matches = _query(_url(alembic_config), _insert_and_read)
    assert "'registr':2A" in vector_text  # heading terms carry weight A
    assert matches == 1


def test_one_active_version_per_document_enforced(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    def _insert_two_active(conn: Connection) -> None:
        for version in (1, 2):
            conn.exec_driver_sql(
                "INSERT INTO documents (id, document_id, version, title, canonical_url, category,"
                " source_type, license, content_hash, retrieved_at, status)"
                f" VALUES (gen_random_uuid(), 'doc-1', {version}, 'T', 'u', 'api', 'documentation',"
                " 'l', 'h', now(), 'active')"
            )

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="uq_documents_one_active_version"):
        _query(_url(alembic_config), _insert_two_active)


def test_models_match_migrations(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    def _diff(conn: Connection) -> list[Any]:
        def include_object(obj: Any, name: str | None, type_: str, *_: Any) -> bool:
            return not (type_ == "table" and name == "alembic_version")

        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": False,
                "include_object": include_object,
            },
        )
        diffs = compare_metadata(ctx, Base.metadata)
        # The ORM declares `vector` without a dimension; the migration fixes it.
        return [
            d
            for d in diffs
            if not (
                isinstance(d, list) and d and d[0][0] == "modify_type" and d[0][3] == "embedding"
            )
        ]

    assert _query(_url(alembic_config), _diff) == []


def test_downgrade_to_base_removes_everything(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    url = _url(alembic_config)
    tables = _query(url, _scalars("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    assert tables & EXPECTED_TABLES == set()
    assert "vector" not in _query(url, _scalars("SELECT extname FROM pg_extension"))


def test_upgrade_downgrade_upgrade_is_repeatable(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0002")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    tables = _query(
        _url(alembic_config),
        _scalars("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"),
    )
    assert EXPECTED_TABLES <= tables


def test_hnsw_dimension_limit_is_enforced(alembic_config: Config) -> None:
    alembic_config.attributes["embedding_dimension"] = 3072
    command.upgrade(alembic_config, "0002")
    with pytest.raises(RuntimeError, match="exceeds the HNSW limit"):
        command.upgrade(alembic_config, "head")


def test_resize_migration_clears_vectors_and_rebuilds_index(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0004")
    url = _url(alembic_config)

    def _seed(conn: Connection) -> None:
        conn.exec_driver_sql(
            "INSERT INTO documents (id, document_id, version, title, canonical_url, category,"
            " source_type, license, content_hash, retrieved_at, status)"
            " VALUES (gen_random_uuid(), 'doc-1', 1, 'T', 'u', 'api', 'documentation',"
            " 'l', 'h', now(), 'active')"
        )
        conn.exec_driver_sql(
            "INSERT INTO document_chunks (id, chunk_id, document_pk, document_id,"
            " document_version, chunk_index, title, heading_path, content, content_hash,"
            " token_count, category, canonical_url, has_code, is_split_oversize, embedding,"
            " embedding_model)"
            " SELECT gen_random_uuid(), 'c1', id, 'doc-1', 1, 0, 'T', 'T', 'text', 'h', 3, 'api',"
            " 'u', false, false, '[1,0,0,0,0,0,0,0]', 'old-model@8' FROM documents"
        )
        conn.commit()

    _query(url, _seed)
    alembic_config.attributes["embedding_dimension"] = 16
    command.upgrade(alembic_config, "head")

    dims = _query(
        url,
        _scalars(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
        ),
    )
    assert dims == {16}
    rows = _query(
        url,
        lambda c: list(c.exec_driver_sql("SELECT embedding, embedding_model FROM document_chunks")),
    )
    assert [tuple(r) for r in rows] == [(None, None)]  # old vectors are incompatible
    indexes = _query(url, _scalars("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"))
    assert "ix_document_chunks_embedding_hnsw" in indexes


def test_resize_migration_is_noop_when_dimension_matches(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")  # 0002 created vector(8); 0005 target is also 8
    dims = _query(
        _url(alembic_config),
        _scalars(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
        ),
    )
    assert dims == {TEST_EMBEDDING_DIMENSION}
