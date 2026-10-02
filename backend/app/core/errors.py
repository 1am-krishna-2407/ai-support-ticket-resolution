"""Error taxonomy and the standard error contract.

Every error leaving the API has the shape::

    {"error": {"code": "...", "message": "...", "details": {...} | null, "request_id": "..."}}

Stack traces and internal exception text are never included.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    INGESTION_ALREADY_RUNNING = "INGESTION_ALREADY_RUNNING"
    EVALUATION_ALREADY_RUNNING = "EVALUATION_ALREADY_RUNNING"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    EMBEDDING_PROVIDER_ERROR = "EMBEDDING_PROVIDER_ERROR"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    GROUNDING_FAILED = "GROUNDING_FAILED"
    CITATION_VALIDATION_FAILED = "CITATION_VALIDATION_FAILED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Base class for domain errors that map to a public error response."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    default_message: str = "An internal error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.message = message or self.default_message
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class InvalidRequestError(AppError):
    code = ErrorCode.INVALID_REQUEST
    status_code = 400
    default_message = "The request is invalid."


class PayloadTooLargeError(AppError):
    code = ErrorCode.PAYLOAD_TOO_LARGE
    status_code = 413
    default_message = "The request body is too large."


class ConflictError(AppError):
    code = ErrorCode.CONFLICT
    status_code = 409
    default_message = "The request conflicts with existing data."


class UnauthorizedError(AppError):
    code = ErrorCode.UNAUTHORIZED
    status_code = 401
    default_message = "Authentication is required for this operation."


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN
    status_code = 403
    default_message = "This operation is not permitted."


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED
    status_code = 429
    default_message = "Too many requests. Please retry later."


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = 404
    default_message = "The requested resource was not found."


class DocumentNotFoundError(AppError):
    code = ErrorCode.DOCUMENT_NOT_FOUND
    status_code = 404
    default_message = "Document not found."


class IngestionAlreadyRunningError(AppError):
    code = ErrorCode.INGESTION_ALREADY_RUNNING
    status_code = 409
    default_message = "An ingestion job is already running."


class EvaluationAlreadyRunningError(AppError):
    code = ErrorCode.EVALUATION_ALREADY_RUNNING
    status_code = 409
    default_message = "An evaluation run is already in progress."


class RetrievalUnavailableError(AppError):
    code = ErrorCode.RETRIEVAL_UNAVAILABLE
    status_code = 503
    default_message = "Retrieval is temporarily unavailable."


class EmbeddingProviderError(AppError):
    code = ErrorCode.EMBEDDING_PROVIDER_ERROR
    status_code = 502
    default_message = "The embedding provider failed to respond correctly."


class EmbeddingQuotaExhaustedError(EmbeddingProviderError):
    """The provider's daily quota is used up; retrying before the quota resets is pointless."""

    status_code = 503
    default_message = "The embedding provider's daily quota is exhausted."


class LLMProviderError(AppError):
    code = ErrorCode.LLM_PROVIDER_ERROR
    status_code = 502
    default_message = "The language model provider failed to respond correctly."


class LLMTimeoutError(LLMProviderError):
    code = ErrorCode.LLM_TIMEOUT
    status_code = 504
    default_message = "The language model provider timed out."


class InvalidModelOutputError(AppError):
    code = ErrorCode.INVALID_MODEL_OUTPUT
    status_code = 502
    default_message = "The model returned output that failed validation."


class ServiceUnavailableError(AppError):
    code = ErrorCode.SERVICE_UNAVAILABLE
    status_code = 503
    default_message = "A required dependency is unavailable."


def build_error_payload(
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details, request_id=request_id)
    )
    return body.model_dump(mode="json")
