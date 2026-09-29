from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.core.config import AdminMode, Settings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, admin_mode="dev")  # type: ignore[call-arg]


def test_defaults_are_valid(settings: Settings) -> None:
    assert settings.admin_mode is AdminMode.TOKEN
    assert settings.retrieval_top_k <= settings.retrieval_candidate_k
    assert settings.log_llm_payloads is False
    assert settings.persist_ticket_bodies is False


def test_database_url_is_secret(settings: Settings) -> None:
    assert "pass" not in repr(settings)
    assert "pass" not in str(settings.database_url)


@pytest.mark.parametrize("token", [None, "", "short"])
def test_token_mode_requires_strong_token(
    make_settings: Callable[..., Settings], token: str | None
) -> None:
    with pytest.raises(ValidationError, match="ADMIN_TOKEN"):
        make_settings(admin_mode="token", admin_token=token)


def test_dev_admin_mode_forbidden_in_production(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="ADMIN_MODE=dev"):
        make_settings(admin_mode="dev", environment="production")


def test_dev_admin_mode_allowed_outside_production(make_settings: Callable[..., Settings]) -> None:
    assert make_settings(admin_mode="dev", admin_token=None).admin_mode is AdminMode.DEV


def test_csv_lists_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("ADMIN_MODE", "local")
    monkeypatch.setenv("ALLOWED_SOURCE_DOMAINS", "docs.gitlab.com, Example.org.")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test,http://b.test")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.allowed_source_domains == ["docs.gitlab.com", "example.org"]
    assert s.cors_origins == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize("domain", ["https://docs.gitlab.com", "docs.gitlab.com/path", "a b"])
def test_allowed_domains_must_be_bare_hostnames(
    make_settings: Callable[..., Settings], domain: str
) -> None:
    with pytest.raises(ValidationError, match="bare hostname"):
        make_settings(allowed_source_domains=[domain])


def test_empty_allowed_domains_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        make_settings(allowed_source_domains="")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"chunk_target_tokens": 100, "chunk_overlap_tokens": 100}, "CHUNK_OVERLAP_TOKENS"),
        ({"chunk_target_tokens": 500, "chunk_hard_max_tokens": 400}, "CHUNK_HARD_MAX_TOKENS"),
        ({"retrieval_top_k": 50, "retrieval_candidate_k": 10}, "RETRIEVAL_TOP_K"),
        ({"log_level": "LOUD"}, "log_level"),
        ({"llm_temperature": 3}, "llm_temperature"),
        ({"embedding_dimension": 0}, "embedding_dimension"),
    ],
)
def test_invalid_values_fail_fast(
    make_settings: Callable[..., Settings], overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(**overrides)
