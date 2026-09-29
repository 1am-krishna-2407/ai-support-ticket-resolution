from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import REDACTED, JsonFormatter, redact_text, redact_value


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
        ("key=sk-proj-ABCDEFGHIJKLMNOPQRSTUV", "sk-proj-ABCDEFGHIJKLMNOPQRSTUV"),
        ("postgresql+asyncpg://app:hunter2@db:5432/x", "hunter2"),
    ],
)
def test_redact_text(raw: str, leaked: str) -> None:
    assert leaked not in redact_text(raw)
    assert REDACTED in redact_text(raw)


def test_redact_value_masks_sensitive_keys_recursively() -> None:
    value = {"headers": {"Authorization": "x", "accept": "json"}, "api_key": "k", "n": 1}
    assert redact_value("payload", value) == {
        "headers": {"Authorization": REDACTED, "accept": "json"},
        "api_key": REDACTED,
        "n": 1,
    }


def test_formatter_emits_json_with_extras_and_no_traceback() -> None:
    logger = logging.getLogger("test.fmt")
    try:
        raise ValueError("boom with sk-ABCDEFGHIJKLMNOPQRSTUV")
    except ValueError:
        record = logger.makeRecord(
            "test.fmt",
            logging.ERROR,
            __file__,
            1,
            "failed",
            None,
            exc_info=__import__("sys").exc_info(),
            extra={"admin_token": "t", "stage": "embed"},
        )
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "failed"
    assert line["stage"] == "embed"
    assert line["admin_token"] == REDACTED
    assert line["exception_type"] == "ValueError"
    assert "sk-ABCDEF" not in json.dumps(line)
