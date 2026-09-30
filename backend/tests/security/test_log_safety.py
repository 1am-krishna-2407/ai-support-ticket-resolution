"""Logs never contain secrets, ticket text, or LLM payloads (Phase 8 exit gate)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import configure_logging, redact_text
from app.providers.llm import ChatMessage, OpenAICompatibleChatProvider
from app.schemas.tickets import TicketInput
from tests.unit.test_ticket_analysis import (
    resolution_json,
    triage_json,
)

GROQ_KEY = "gsk_" + "A1b2C3d4" * 6
NVIDIA_KEY = "nvapi-" + "Z9y8X7w6" * 8
HF_KEY = "hf_" + "Q1w2E3r4" * 4 + "t5"
GITLAB_PAT = "glpat-" + "k" * 20
SECRET_TICKET = TicketInput(
    title="Runner TLS error on gitlab.internal",
    description=f"x509: certificate signed by unknown authority. My token is {GITLAB_PAT}.",
)


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    yield stream
    configure_logging("INFO")


@pytest.mark.parametrize(
    "secret",
    [
        GROQ_KEY,
        NVIDIA_KEY,
        HF_KEY,
        GITLAB_PAT,
        "AIza" + "S" * 35,
        "AQ." + "Ab8" * 15,
        "Bearer abc.def.ghi",
        "postgresql+asyncpg://support:hunter2hunter2@db:5432/support",
    ],
)
def test_known_secret_formats_are_redacted(secret: str) -> None:
    redacted = redact_text(f"error for {secret} here")
    assert "hunter2" not in redacted and "[REDACTED]" in redacted
    if not secret.startswith("postgres"):
        assert secret not in redacted


async def test_analysis_logs_no_ticket_text_or_model_output(
    build: Callable[..., object], captured: io.StringIO
) -> None:
    service, _ = build({"triage": [triage_json()], "resolution": [resolution_json()]})  # type: ignore[operator]
    await service.analyze(SECRET_TICKET)
    logs = captured.getvalue()
    for forbidden in (
        GITLAB_PAT,
        "certificate signed by unknown authority",
        "gitlab.internal",
        "The runner does not trust the GitLab certificate.",  # model summary
        "gitlab-runner restart",  # model step
    ):
        assert forbidden not in logs


async def test_provider_errors_are_logged_as_redacted_metadata(
    make_settings: Callable[..., Settings], captured: io.StringIO
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A hostile or careless provider echoes the key and the prompt in its error.
        return httpx.Response(
            400,
            json={
                "error": {"code": "bad", "message": f"bad key {NVIDIA_KEY}; prompt=SECRETPROMPT"}
            },
        )

    settings = make_settings(
        llm_provider="nvidia", nemotron_api_key=NVIDIA_KEY, llm_api_key=GROQ_KEY, llm_max_retries=0
    )
    provider = OpenAICompatibleChatProvider(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(Exception, match="HTTP 400"):
        await provider.complete_json(
            [ChatMessage("user", "SECRETPROMPT ticket body")], schema_name="s", schema={}
        )
    logs = captured.getvalue()
    assert NVIDIA_KEY not in logs and GROQ_KEY not in logs
    assert "ticket body" not in logs
    events = [json.loads(line) for line in logs.splitlines() if line.strip()]
    assert any(e["message"] == "llm request failed" for e in events)


def test_payload_logging_is_off_by_default(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().log_llm_payloads is False


def test_settings_repr_never_shows_keys(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(nemotron_api_key=NVIDIA_KEY, hf_token=HF_KEY, llm_api_key=GROQ_KEY)
    text = repr(settings) + str(settings.model_dump())
    assert NVIDIA_KEY not in text and HF_KEY not in text and GROQ_KEY not in text
    logging.getLogger("t").info("settings", extra={"settings": settings.model_dump()})
