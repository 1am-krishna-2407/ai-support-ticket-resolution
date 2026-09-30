"""Documented error responses for OpenAPI, all in the standard error contract."""

from __future__ import annotations

from typing import Any

from app.core.errors import ErrorResponse

_DESCRIPTIONS = {
    400: "The request is invalid (`INVALID_REQUEST`).",
    401: "Missing admin token (`UNAUTHORIZED`).",
    403: "Invalid admin token or admin not allowed from this client (`FORBIDDEN`).",
    404: "Resource not found (`NOT_FOUND`, `DOCUMENT_NOT_FOUND`).",
    409: "A job of this kind is already running (`*_ALREADY_RUNNING`).",
    413: "Request body exceeds `MAX_REQUEST_BODY_BYTES` (`PAYLOAD_TOO_LARGE`).",
    422: "Request validation failed (`INVALID_REQUEST`); input values are never echoed.",
    429: "Rate limit exceeded (`RATE_LIMITED`); see the `Retry-After` header.",
    502: "A provider returned an error (`EMBEDDING_PROVIDER_ERROR`, `LLM_PROVIDER_ERROR`).",
    503: "A dependency is unavailable (`RETRIEVAL_UNAVAILABLE`, `SERVICE_UNAVAILABLE`).",
}


def errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in status_codes
    }


READ = errors(422)
BODY = errors(413, 422)
ADMIN = errors(401, 403, 409, 413, 422)
