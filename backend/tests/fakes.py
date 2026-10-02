"""Test doubles. These live only under tests/ and are never importable from app/."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.errors import EmbeddingProviderError
from app.providers.embeddings import EmbeddingInput
from app.services.ingestion.fetcher import FetchError, FetchErrorKind, FetchResult


class FakeFetcher:
    """Serves pages from a dict: url -> HTML string, or an exception to raise."""

    def __init__(self, pages: dict[str, str | Exception] | None = None) -> None:
        self.pages: dict[str, str | Exception] = dict(pages or {})
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        page = self.pages.get(url)
        if page is None:
            raise FetchError(FetchErrorKind.HTTP_STATUS, f"HTTP 404 for {url}", status=404)
        if isinstance(page, Exception):
            raise page
        return FetchResult(
            requested_url=url,
            final_url=url,
            status=200,
            content_type="text/html",
            body=page.encode("utf-8"),
            fetched_at=datetime.now(UTC),
        )

    async def aclose(self) -> None:
        return None


def deterministic_vector(text: str, dimension: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [(digest[i % len(digest)] - 127.5) / 127.5 for i in range(dimension)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


class FakeEmbeddingProvider:
    def __init__(self, dimension: int = 8, batch_size: int = 16) -> None:
        self._dimension = dimension
        self._batch_size = batch_size
        self._requests = 0
        self.embedded_texts: list[str] = []
        self.fail_when_text_contains: str | None = None
        self.failure: Exception | None = None  # raised instead of the default error

    @property
    def spec(self) -> str:
        return f"fake-embedding@{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_input_tokens(self) -> int:
        return 1800

    @property
    def requests_made(self) -> int:
        return self._requests

    async def embed_documents(self, items: Sequence[EmbeddingInput]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            self._requests += 1
            for item in batch:
                if self.fail_when_text_contains and self.fail_when_text_contains in item.text:
                    raise self.failure or EmbeddingProviderError("fake provider failure")
                self.embedded_texts.append(item.text)
                vectors.append(deterministic_vector(item.text, self._dimension))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        self._requests += 1
        return deterministic_vector(text, self._dimension)

    async def aclose(self) -> None:
        return None


class FakeLLMProvider:
    """Scripted LLM: responses are queued per schema name (JSON strings or exceptions)."""

    def __init__(self, script: dict[str, list[object]] | None = None) -> None:
        self.script: dict[str, list[object]] = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, list[object]]] = []
        self.reasoning: dict[str, bool] = {}

    @property
    def model(self) -> str:
        return "fake-llm"

    async def complete_json(
        self, messages, *, schema_name, schema, max_tokens=None, reasoning=True
    ):  # type: ignore[no-untyped-def]
        from app.providers.llm import LLMResult

        self.calls.append((schema_name, list(messages)))
        self.reasoning[schema_name] = reasoning
        queue = self.script.get(schema_name)
        if not queue:
            raise AssertionError(f"unexpected LLM call for schema {schema_name!r}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(
            content=str(item),
            model="fake-llm",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1.0,
        )

    def calls_for(self, schema_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == schema_name)

    async def aclose(self) -> None:
        return None
