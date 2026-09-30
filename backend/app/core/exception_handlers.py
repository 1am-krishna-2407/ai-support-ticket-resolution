"""Map every exception to the standard error contract. No stack traces leave the API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, ErrorCode, build_error_payload
from app.core.logging import request_id_var

logger = logging.getLogger("app.errors")

_HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.INVALID_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    422: ErrorCode.INVALID_REQUEST,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


def _response(
    status_code: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=build_error_payload(
            code, message, details=details, request_id=request_id_var.get()
        ),
        headers=headers,
    )


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    request.state.error_code = exc.code.value
    if exc.status_code >= 500:
        logger.warning("dependency or internal error", extra={"error_code": exc.code.value})
    return _response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    request.state.error_code = ErrorCode.INVALID_REQUEST.value
    # Echo locations and messages only; never echo the submitted input values.
    errors = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    return _response(
        422, ErrorCode.INVALID_REQUEST, "Request validation failed.", {"errors": errors}
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return _response(exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return _response(500, ErrorCode.INTERNAL_ERROR, "An internal error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
