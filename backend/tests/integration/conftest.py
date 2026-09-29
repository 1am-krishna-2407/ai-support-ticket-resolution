from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_EMBEDDING_DIMENSION = 8


def _load_dotenv_value(name: str) -> str | None:
    env_file = BACKEND_DIR.parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip()
    return None


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or _load_dotenv_value("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; integration tests need real Postgres+pgvector")
    if not url.rstrip("/").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point at a database whose name ends in '_test'")
    return url


def _reset_schema(url: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            await conn.exec_driver_sql("CREATE SCHEMA public")
            await conn.exec_driver_sql("DROP EXTENSION IF EXISTS vector")
        await engine.dispose()

    asyncio.run(_run())


@pytest.fixture
def alembic_config(test_database_url: str) -> Iterator[Config]:
    """Alembic config bound to a freshly wiped test database."""
    _reset_schema(test_database_url)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = test_database_url
    cfg.attributes["embedding_dimension"] = TEST_EMBEDDING_DIMENSION
    yield cfg
    _reset_schema(test_database_url)
