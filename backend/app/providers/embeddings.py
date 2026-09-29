"""Embedding providers (ADR D4/D5).

Both providers share batching, bounded concurrency, request pacing, retries with
backoff, count/dimension validation, and L2 normalisation (`_HttpEmbeddingProvider`).
They differ only in URL, authentication, payload, response parsing, and quota signals.

`HuggingFaceEmbeddingProvider` (default): Qwen3-Embedding-0.6B through the Hugging Face
Inference Providers router, which forwards to DeepInfra's OpenAI-style embeddings API
(`POST {base}/embeddings`, `{"model", "input", "dimensions"}` → `data[].embedding`).
Verified 2026-09-29: 1024-dim unit vectors; `dimensions` is honoured. Queries are
prefixed with Qwen3's instruction format (`Instruct: {task}\\nQuery:{text}`); documents
are embedded as-is, as the model card recommends. HTTP 402 is retried (it can be transient)
and only treated as exhausted credits if it persists through every retry.

`GeminiEmbeddingProvider`: Gemini's native `batchEmbedContents` with top-level
`taskType`, `title`, and `outputDimensionality` fields. Verified 2026-09-29: batch
requests ignore `embedContentConfig` and reject `autoTruncate`, so inputs over 2048
tokens are truncated silently; `max_input_tokens` keeps chunks far below that. A
per-day quota 429 fails fast (Google's "retry in N seconds" hint is misleading there).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx

from app.core.config import EmbeddingProviderName, Settings
from app.core.errors import EmbeddingProviderError, EmbeddingQuotaExhaustedError
from app.core.logging import redact_text

logger = logging.getLogger("app.providers.embeddings")

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_BACKOFF_SECONDS = 60.0
_DURATION = re.compile(r"(\d+(?:\.\d+)?)s")


class EmbeddingTask(StrEnum):
    DOCUMENT = "RETRIEVAL_DOCUMENT"
    QUERY = "RETRIEVAL_QUERY"


@dataclass(frozen=True)
class EmbeddingInput:
    text: str
    title: str | None = None


class EmbeddingProvider(Protocol):
    @property
    def spec(self) -> str:
        """Identifies model + settings; embeddings are only reused when the spec matches."""
        ...

    @property
    def dimension(self) -> int: ...

    @property
    def max_input_tokens(self) -> int:
        """Largest chunk (in our tokenizer's tokens) that is safe to send."""
        ...

    @property
    def requests_made(self) -> int: ...

    async def embed_documents(self, items: Sequence[EmbeddingInput]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def aclose(self) -> None: ...


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0 or not math.isfinite(norm):
        raise EmbeddingProviderError("The embedding provider returned a zero or invalid vector.")
    return [v / norm for v in vector]


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _retry_delay(response: httpx.Response) -> float | None:
    """Server-requested wait: `Retry-After` header, or Google's RetryInfo in the error body."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    body = _json(response)
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details", []) if isinstance(error, dict) else []
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("RetryInfo"):
            match = _DURATION.fullmatch(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _google_quota_ids(response: httpx.Response) -> list[str]:
    """Quota identifiers from Google's QuotaFailure details (which limit was hit)."""
    body = _json(response)
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details", []) if isinstance(error, dict) else []
    ids: list[str] = []
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("QuotaFailure"):
            for violation in detail.get("violations", []) or []:
                if isinstance(violation, dict) and violation.get("quotaId"):
                    ids.append(str(violation["quotaId"]))
    return ids


class _HttpEmbeddingProvider:
    name: str = "embedding"

    def __init__(
        self,
        settings: Settings,
        *,
        headers: dict[str, str],
        transport: httpx.AsyncBaseTransport | None,
        sleep: Callable[[float], Awaitable[None]],
        clock: Callable[[], float],
    ) -> None:
        self._model = settings.embedding_model
        self._dimension = settings.embedding_dimension
        self._batch_size = settings.embedding_batch_size
        self._max_retries = settings.embedding_max_retries
        self._min_interval = 60.0 / settings.embedding_max_requests_per_minute
        self._semaphore = asyncio.Semaphore(settings.embedding_concurrency)
        self._pace_lock = asyncio.Lock()
        self._last_request: float | None = None
        self._sleep = sleep
        self._clock = clock
        self._requests_made = 0
        # Most recent failure (cleared by the next success); reported by GET /ready so
        # health checks never spend provider credits on probe requests.
        self.last_failure: dict[str, Any] | None = None
        self._client = httpx.AsyncClient(
            base_url=settings.resolved_embedding_base_url.rstrip("/") + "/",
            transport=transport,
            timeout=settings.embedding_timeout_seconds,
            headers={"Content-Type": "application/json", **headers},
            trust_env=False,
        )

    # --- provider hooks ------------------------------------------------------------

    def _path(self) -> str:
        raise NotImplementedError

    def _request_body(self, items: Sequence[EmbeddingInput], task: EmbeddingTask) -> dict[str, Any]:
        raise NotImplementedError

    def _extract_vectors(self, body: Any) -> list[list[float]]:
        raise NotImplementedError

    retryable_status: frozenset[int] = RETRYABLE_STATUS

    def _quota_exhausted(
        self, response: httpx.Response, *, final_attempt: bool
    ) -> list[str] | None:
        """Quota identifiers when the provider says its budget is used up, else None."""
        return None

    # --- public API ----------------------------------------------------------------

    @property
    def spec(self) -> str:
        return f"{self._model}@{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_input_tokens(self) -> int:
        raise NotImplementedError

    @property
    def requests_made(self) -> int:
        return self._requests_made

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed_documents(self, items: Sequence[EmbeddingInput]) -> list[list[float]]:
        batches = [
            list(items[i : i + self._batch_size]) for i in range(0, len(items), self._batch_size)
        ]
        results = await asyncio.gather(
            *(self._embed_batch(batch, EmbeddingTask.DOCUMENT) for batch in batches)
        )
        return [vector for batch in results for vector in batch]

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await self._embed_batch([EmbeddingInput(text)], EmbeddingTask.QUERY)
        return vector

    # --- shared request loop ---------------------------------------------------------

    async def _pace(self) -> None:
        async with self._pace_lock:
            if self._last_request is not None:
                wait = self._min_interval - (self._clock() - self._last_request)
                if wait > 0:
                    await self._sleep(wait)
            self._last_request = self._clock()

    async def _embed_batch(
        self, items: Sequence[EmbeddingInput], task: EmbeddingTask
    ) -> list[list[float]]:
        try:
            vectors = await self._embed_batch_once(items, task)
        except EmbeddingProviderError as exc:
            self.last_failure = {
                "code": exc.code.value,
                "message": exc.message,
                "at": time.time(),
                "quota_exhausted": isinstance(exc, EmbeddingQuotaExhaustedError),
            }
            raise
        self.last_failure = None
        return vectors

    async def _embed_batch_once(
        self, items: Sequence[EmbeddingInput], task: EmbeddingTask
    ) -> list[list[float]]:
        if not items:
            return []
        body = self._request_body(items, task)
        async with self._semaphore:
            for attempt in range(self._max_retries + 1):
                last = attempt == self._max_retries
                await self._pace()
                self._requests_made += 1
                try:
                    response = await self._client.post(self._path(), json=body)
                except httpx.TimeoutException as exc:
                    if last:
                        raise EmbeddingProviderError(
                            "The embedding provider timed out.", details={"attempts": attempt + 1}
                        ) from exc
                    await self._sleep(self._backoff(attempt, None))
                    continue
                except httpx.TransportError as exc:
                    if last:
                        raise EmbeddingProviderError(
                            "Could not reach the embedding provider.",
                            details={"attempts": attempt + 1, "error": type(exc).__name__},
                        ) from exc
                    await self._sleep(self._backoff(attempt, None))
                    continue

                quota = self._quota_exhausted(response, final_attempt=last)
                if quota is not None:
                    raise EmbeddingQuotaExhaustedError(
                        "The embedding provider's quota or credits are exhausted; "
                        "retry after they reset or are topped up.",
                        details={"quota": quota, "status": response.status_code},
                    )
                if response.status_code in self.retryable_status and not last:
                    delay = self._backoff(attempt, _retry_delay(response))
                    logger.warning(
                        "embedding request will be retried",
                        extra={
                            **self._error_details(response, attempt + 1),
                            "retry_in_seconds": round(delay, 1),
                        },
                    )
                    await self._sleep(delay)
                    continue
                if response.status_code != 200:
                    raise EmbeddingProviderError(
                        f"The embedding provider returned HTTP {response.status_code}.",
                        details=self._error_details(response, attempt + 1),
                    )
                return self._validate(response, expected=len(items))
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _backoff(attempt: int, retry_delay: float | None) -> float:
        if retry_delay is not None:
            return min(retry_delay, MAX_BACKOFF_SECONDS)
        return min(2.0**attempt, MAX_BACKOFF_SECONDS) + random.uniform(0, 0.5)  # noqa: S311

    @staticmethod
    def _error_details(response: httpx.Response, attempts: int) -> dict[str, Any]:
        details: dict[str, Any] = {"status": response.status_code, "attempts": attempts}
        body = _json(response)
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            details["provider_status"] = error.get("status") or error.get("type")
            message = str(error.get("message", ""))
        elif isinstance(error, str):
            message = error
        else:
            return details
        details["provider_message"] = redact_text(message[:300])
        return details

    def _validate(self, response: httpx.Response, *, expected: int) -> list[list[float]]:
        try:
            vectors = self._extract_vectors(response.json())
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise EmbeddingProviderError(
                "The embedding provider returned an unexpected response shape."
            ) from exc
        if len(vectors) != expected:
            raise EmbeddingProviderError(
                "The embedding provider returned the wrong number of vectors.",
                details={"expected": expected, "received": len(vectors)},
            )
        for vector in vectors:
            if len(vector) != self._dimension:
                raise EmbeddingProviderError(
                    "The embedding provider returned vectors of the wrong dimension.",
                    details={"expected": self._dimension, "received": len(vector)},
                )
        return [l2_normalize(v) for v in vectors]


class HuggingFaceEmbeddingProvider(_HttpEmbeddingProvider):
    """OpenAI-style embeddings through the Hugging Face Inference Providers router."""

    name = "huggingface"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        key = settings.resolved_embedding_api_key
        if key is None or not key.get_secret_value():
            raise EmbeddingProviderError("HF_TOKEN is not configured.")
        super().__init__(
            settings,
            headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            transport=transport,
            sleep=sleep,
            clock=clock,
        )
        self._instruction = settings.embedding_query_instruction

    @property
    def max_input_tokens(self) -> int:
        return 8000  # Qwen3-Embedding accepts 32k tokens; keep requests small and fast.

    def _path(self) -> str:
        return "embeddings"

    def _request_body(self, items: Sequence[EmbeddingInput], task: EmbeddingTask) -> dict[str, Any]:
        if task is EmbeddingTask.QUERY and self._instruction:
            texts = [f"Instruct: {self._instruction}\nQuery:{item.text}" for item in items]
        else:
            texts = [item.text for item in items]
        return {
            "model": self._model,
            "input": texts,
            "dimensions": self._dimension,
            "encoding_format": "float",
        }

    def _extract_vectors(self, body: Any) -> list[list[float]]:
        data = sorted(body["data"], key=lambda item: int(item.get("index", 0)))
        return [list(map(float, item["embedding"])) for item in data]

    # Verified 2026-09-29: the router occasionally answers 402 Payment Required for a request
    # that succeeds moments later, so 402 is retried; only a 402 that persists through every
    # retry is treated as "credits used up".
    retryable_status = RETRYABLE_STATUS | {402}

    def _quota_exhausted(
        self, response: httpx.Response, *, final_attempt: bool
    ) -> list[str] | None:
        if response.status_code == 402 and final_attempt:
            return ["huggingface-credits"]
        return None


class GeminiEmbeddingProvider(_HttpEmbeddingProvider):
    name = "gemini"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        key = settings.embedding_api_key
        if key is None or not key.get_secret_value():
            raise EmbeddingProviderError("EMBEDDING_API_KEY is not configured.")
        super().__init__(
            settings,
            headers={"x-goog-api-key": key.get_secret_value()},
            transport=transport,
            sleep=sleep,
            clock=clock,
        )
        self._model = settings.embedding_model.removeprefix("models/")

    @property
    def max_input_tokens(self) -> int:
        # Gemini accepts 2048 tokens; our tokenizer differs from Gemini's, so keep a margin.
        return 1800

    def _path(self) -> str:
        return f"models/{self._model}:batchEmbedContents"

    def _request_body(self, items: Sequence[EmbeddingInput], task: EmbeddingTask) -> dict[str, Any]:
        requests = []
        for item in items:
            request: dict[str, Any] = {
                "model": f"models/{self._model}",
                "content": {"parts": [{"text": item.text}]},
                "taskType": task.value,
                "outputDimensionality": self._dimension,
            }
            if task is EmbeddingTask.DOCUMENT and item.title:
                request["title"] = item.title
            requests.append(request)
        return {"requests": requests}

    def _extract_vectors(self, body: Any) -> list[list[float]]:
        return [list(map(float, e["values"])) for e in body["embeddings"]]

    def _quota_exhausted(
        self, response: httpx.Response, *, final_attempt: bool
    ) -> list[str] | None:
        # A per-day cap fails fast: retrying before the daily reset cannot succeed.
        if response.status_code != 429:
            return None
        quota = _google_quota_ids(response)
        return quota if any("PerDay" in q for q in quota) else None


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider is EmbeddingProviderName.GEMINI:
        return GeminiEmbeddingProvider(settings)
    return HuggingFaceEmbeddingProvider(settings)
