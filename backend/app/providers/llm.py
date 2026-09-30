"""LLM providers (ADR D4).

`OpenAICompatibleChatProvider` calls an OpenAI-compatible `/chat/completions` endpoint;
the default configuration points at Groq with `openai/gpt-oss-120b`. Every call uses
strict JSON-schema structured output, so the model can only return the requested shape.
Returned text is still parsed and validated by the caller: strict mode is a guard,
not a guarantee.

Prompt and completion text is never logged unless LOG_LLM_PAYLOADS is enabled, and the
model's reasoning text is never returned or logged.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from app.core.config import LLMProviderName, Settings
from app.core.errors import InvalidModelOutputError, LLMProviderError, LLMTimeoutError
from app.core.logging import redact_text
from app.core.tokenizer import get_tokenizer

logger = logging.getLogger("app.providers.llm")

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_BACKOFF_SECONDS = 30.0
# Provider error codes meaning "model output failed schema validation" (Groq strict mode).
SCHEMA_FAILURE_CODES = frozenset({"json_validate_failed"})


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResult:
    content: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float


class LLMProvider(Protocol):
    @property
    def model(self) -> str: ...

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> LLMResult: ...

    async def aclose(self) -> None: ...


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema adapted to strict structured-output rules.

    Inlines $refs, makes every property required (optional fields stay nullable),
    forbids additional properties, and strips titles/defaults that strict mode rejects.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"])
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(raw)  # type: ignore[no-any-return]


def _daily_limit(response: httpx.Response) -> bool:
    """True when a 429 is a per-day quota (retrying within minutes cannot succeed)."""
    try:
        message = str(response.json().get("error", {}).get("message", ""))
    except ValueError:
        return False
    lowered = message.lower()
    return "per day" in lowered or "(tpd)" in lowered or "(rpd)" in lowered


class TokenBucket:
    """Tokens-per-minute pacing: waits until a request's estimated tokens are available.

    A request larger than the whole budget is let through once the bucket is full, so the
    provider (not the client) decides; the context budget keeps requests below that size.
    """

    def __init__(
        self,
        tokens_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.capacity = float(tokens_per_minute)
        self.rate = tokens_per_minute / 60.0
        self._tokens = self.capacity
        self._updated = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, tokens: int) -> float:
        """Reserve `tokens`; returns the seconds waited."""
        need = min(float(tokens), self.capacity)
        waited = 0.0
        async with self._lock:
            self._refill()
            while self._tokens < need:
                delay = (need - self._tokens) / self.rate
                await self._sleep(delay)
                waited += delay
                self._refill()
            self._tokens -= need
        return waited


class OpenAICompatibleChatProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        api_key = settings.resolved_llm_api_key
        if api_key is None:
            name = (
                "NEMOTRON_API_KEY"
                if settings.llm_provider is LLMProviderName.NVIDIA
                else "LLM_API_KEY"
            )
            raise LLMProviderError(f"{name} is not configured.")
        self._settings = settings
        self._sleep = sleep
        self.last_failure: dict[str, Any] | None = None  # reported by GET /ready
        self._count = get_tokenizer(settings.tokenizer_encoding).count
        self._bucket = (
            TokenBucket(settings.llm_tokens_per_minute, clock=clock, sleep=sleep)
            if settings.llm_tokens_per_minute
            else None
        )
        # Request-count pacing: a bucket of N "tokens" per minute, one per request.
        self._request_bucket = (
            TokenBucket(settings.llm_requests_per_minute, clock=clock, sleep=sleep)
            if settings.llm_requests_per_minute
            else None
        )
        self._client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/") + "/",
            transport=transport,
            timeout=settings.llm_timeout_seconds,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            trust_env=False,
        )

    @property
    def model(self) -> str:
        return self._settings.llm_model

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(
        self,
        messages: Sequence[ChatMessage],
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int | None,
    ) -> dict[str, Any]:
        s = self._settings
        body: dict[str, Any] = {
            "model": s.llm_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": s.llm_temperature,
            "max_completion_tokens": (max_tokens or s.llm_max_output_tokens)
            + s.llm_reasoning_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        if s.llm_seed is not None:
            body["seed"] = s.llm_seed
        if s.llm_provider is LLMProviderName.NVIDIA:
            # NVIDIA's API takes max_tokens; Nemotron's reasoning is a chat-template switch and
            # arrives in a separate reasoning_content field, which is never read.
            body["max_tokens"] = body.pop("max_completion_tokens")
            body["chat_template_kwargs"] = {"enable_thinking": s.llm_enable_thinking}
        elif "gpt-oss" in s.llm_model:
            body["reasoning_effort"] = s.llm_reasoning_effort
            body["include_reasoning"] = False  # never return the model's reasoning text
        return body

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> LLMResult:
        try:
            result = await self._complete_json(messages, schema_name, schema, max_tokens)
        except LLMProviderError as exc:
            self.last_failure = {"code": exc.code.value, "message": exc.message, "at": time.time()}
            raise
        self.last_failure = None
        return result

    async def _complete_json(
        self,
        messages: Sequence[ChatMessage],
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int | None,
    ) -> LLMResult:
        body = self._body(messages, schema_name, schema, max_tokens)
        if self._bucket is not None:
            # Estimate: message text + schema (sent as part of the request) + max output.
            estimate = (
                sum(self._count(m.content) for m in messages)
                + len(json.dumps(schema)) // 4
                + int(body.get("max_completion_tokens") or body["max_tokens"])
            )
            waited = await self._bucket.acquire(estimate)
            if waited > 0.5:
                logger.info(
                    "llm request paced for tokens-per-minute limit",
                    extra={"waited_seconds": round(waited, 1), "estimated_tokens": estimate},
                )
        if self._request_bucket is not None:
            await self._request_bucket.acquire(1)
        if self._settings.log_llm_payloads:
            logger.info("llm request", extra={"payload": body})
        retries = self._settings.llm_max_retries
        for attempt in range(retries + 1):
            last = attempt == retries
            started = time.perf_counter()
            try:
                response = await self._client.post("chat/completions", json=body)
            except httpx.TimeoutException as exc:
                if last:
                    raise LLMTimeoutError(details={"attempts": attempt + 1}) from exc
                await self._sleep(self._backoff(attempt, None))
                continue
            except httpx.TransportError as exc:
                if last:
                    raise LLMProviderError(
                        "Could not reach the language model provider.",
                        details={"attempts": attempt + 1, "error": type(exc).__name__},
                    ) from exc
                await self._sleep(self._backoff(attempt, None))
                continue
            latency = round((time.perf_counter() - started) * 1000, 2)

            if response.status_code == 429 and _daily_limit(response):
                details = self._error_details(response, attempt + 1)
                logger.warning("llm daily token limit reached", extra=details)
                raise LLMProviderError(
                    "The language model provider's daily token limit is reached; "
                    "retry later or upgrade the provider plan.",
                    details=details | {"daily_limit": True},
                )
            if response.status_code in RETRYABLE_STATUS and not last:
                delay = self._backoff(attempt, response.headers.get("retry-after"))
                logger.warning(
                    "llm request will be retried",
                    extra={"status": response.status_code, "retry_in_seconds": round(delay, 1)},
                )
                await self._sleep(delay)
                continue
            if response.status_code != 200:
                details = self._error_details(response, attempt + 1)
                # Metadata only (status, provider code, redacted message); never the prompt.
                logger.warning("llm request failed", extra=details)
                if details.get("provider_code") in SCHEMA_FAILURE_CODES:
                    # The model's output did not satisfy the strict schema: an output problem
                    # the caller can correct once, not a provider outage.
                    raise InvalidModelOutputError(
                        "The model output did not match the required schema.", details=details
                    )
                raise LLMProviderError(
                    f"The language model provider returned HTTP {response.status_code}.",
                    details=details,
                )
            return self._parse(response, latency)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2.0**attempt, MAX_BACKOFF_SECONDS) + random.uniform(0, 0.5)  # noqa: S311

    @staticmethod
    def _error_details(response: httpx.Response, attempts: int) -> dict[str, Any]:
        details: dict[str, Any] = {"status": response.status_code, "attempts": attempts}
        try:
            error = response.json().get("error", {})
        except ValueError:
            return details
        if isinstance(error, dict):
            details["provider_code"] = error.get("code") or error.get("type")
            details["provider_message"] = redact_text(str(error.get("message", ""))[:300])
        return details

    def _parse(self, response: httpx.Response, latency_ms: float) -> LLMResult:
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("The language model returned an unexpected response.") from exc
        if finish == "length":
            raise InvalidModelOutputError(
                "The model output was truncated.", details={"finish_reason": finish}
            )
        if not isinstance(content, str) or not content.strip():
            raise InvalidModelOutputError("The model returned an empty response.")
        usage = data.get("usage") or {}
        if self._settings.log_llm_payloads:
            logger.info("llm response", extra={"payload": content})
        return LLMResult(
            content=content,
            model=str(data.get("model") or self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )
