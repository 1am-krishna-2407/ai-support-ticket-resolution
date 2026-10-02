"""ASGI middleware: request IDs, request logging, and body-size limits."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode, build_error_payload
from app.core.logging import request_id_var
from app.services.request_log import SKIPPED_ROUTES

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")

logger = logging.getLogger("app.request")


async def _send_json_error(send: Send, status: int, code: ErrorCode, message: str) -> None:
    body = json.dumps(build_error_payload(code, message, request_id=request_id_var.get())).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = str(value.decode("latin-1"))
            if _VALID_REQUEST_ID.match(candidate):
                return candidate
            return None
    return None


def _record(scope: Scope, request_id: str, status_code: int, latency_ms: float) -> None:
    """Hand one metadata row to the request-log writer (API routes only)."""
    app = scope.get("app")
    writer = getattr(getattr(app, "state", None), "request_log_writer", None)
    route = scope.get("route")
    template = getattr(route, "path", None)
    if writer is None or not template:
        return  # no writer, or no matched route (404s, 405s, rejected bodies)
    prefix = getattr(getattr(app, "state", None), "api_prefix", "") or ""
    if prefix and template.startswith(prefix):
        relative = template[len(prefix) :]
    else:
        relative, template = template, prefix + template
    if relative in SKIPPED_ROUTES:
        return
    state = scope.get("state", {})
    extra = state.get("request_log") or {}
    writer.submit(
        {
            "request_id": request_id,
            "method": str(scope.get("method", ""))[:10],
            "path": template[:500],
            "route_class": relative.strip("/").split("/")[0][:50] or None,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "error_code": state.get("error_code"),
            "stage_timings": extra.get("stage_timings"),
            "outcome": extra.get("outcome"),
            "user_id": state.get("user_id"),
        }
    )


class RequestContextMiddleware:
    """Assigns a request ID, exposes it in the response, and logs one line per request.

    A client-supplied `X-Request-ID` is honoured only if it is short and uses a
    safe character set; otherwise a fresh UUID is generated.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # Starlette's catch-all handler runs outside this middleware, where the
            # request ID is no longer set, so unhandled errors are answered here.
            logger.error("unhandled exception", exc_info=exc)
            if response_started:
                raise
            await _send_json_error(
                send_wrapper, 500, ErrorCode.INTERNAL_ERROR, "An internal error occurred."
            )
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                },
            )
            _record(scope, request_id, status_code, latency_ms)
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than `max_bytes` with the standard error contract.

    Checks Content-Length up front, then buffers the body while counting bytes, so
    chunked uploads without a Content-Length cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send: Send) -> None:
        await _send_json_error(
            send, 413, ErrorCode.PAYLOAD_TOO_LARGE, f"Request body exceeds {self.max_bytes} bytes."
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared > self.max_bytes:
                    await self._reject(send)
                    return

        # Buffer the body (bounded by max_bytes) and replay it. Raising from inside
        # `receive` would be swallowed by FastAPI's body parser and turned into a 400.
        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            received += len(body)
            if received > self.max_bytes:
                await self._reject(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
