"""Structured JSON logging with request-ID propagation and secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes present on every LogRecord; anything else passed via `extra=` is emitted.
_RESERVED_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)

_SENSITIVE_KEY = re.compile(
    r"(authorization|api[_-]?key|token|secret|password|cookie|database_url)", re.IGNORECASE
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    # Provider keys used by this platform (Groq, NVIDIA, Hugging Face, Google) and GitLab
    # tokens that customers paste into tickets.
    re.compile(r"\b(?:gsk|nvapi|hf|glpat|glrt|gldt|glptt)[_-][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bAQ\.[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)(postgres(?:ql)?(?:\+asyncpg)?://[^:\s]+:)[^@\s]+(@)"),
)
REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            text = pattern.sub(rf"\1{REDACTED}\2", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and key != "request_id" and not key.startswith("_"):
                payload[key] = redact_value(key, value)
        if record.exc_info:
            # Exception type only; full tracebacks can contain request data or secrets.
            exc_type = record.exc_info[0]
            payload["exception_type"] = exc_type.__name__ if exc_type else None
        return json.dumps(payload, default=str)


_HANDLER_NAME = "app-json"
_base_record_factory = logging.getLogRecordFactory()


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    # Capture the request ID when the record is created, not when it is formatted,
    # so buffered or deferred handlers still see the right value.
    record = _base_record_factory(*args, **kwargs)
    record.request_id = request_id_var.get()
    return record


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    logging.setLogRecordFactory(_record_factory)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.set_name(_HANDLER_NAME)
    root = logging.getLogger()
    # Replace only our own handler so repeated calls don't duplicate output and
    # handlers installed by the host (e.g. test capture) are left alone.
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    # Uvicorn's access log duplicates our request log and is unstructured.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
