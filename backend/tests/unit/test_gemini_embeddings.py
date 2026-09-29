from __future__ import annotations

import json
import math
from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import EmbeddingProviderError, EmbeddingQuotaExhaustedError
from app.providers.embeddings import EmbeddingInput, GeminiEmbeddingProvider, l2_normalize

DIM = 4
KEY = "test-gemini-key-000000"


@pytest.fixture
def gemini_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(
        embedding_provider="gemini",
        embedding_api_key=KEY,
        embedding_model="gemini-embedding-001",
        embedding_dimension=DIM,
        embedding_batch_size=2,
        embedding_max_retries=2,
        embedding_max_requests_per_minute=600,
    )


def _ok(request: httpx.Request, scale: float = 2.0) -> httpx.Response:
    n = len(json.loads(request.content)["requests"])
    return httpx.Response(
        200, json={"embeddings": [{"values": [scale * (i + 1)] * DIM} for i in range(n)]}
    )


def _provider(settings: Settings, handler: Callable[[httpx.Request], httpx.Response]):  # type: ignore[no-untyped-def]
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    provider = GeminiEmbeddingProvider(
        settings, transport=httpx.MockTransport(handler), sleep=sleep
    )
    return provider, sleeps


async def test_request_shape_and_auth(gemini_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    provider, _ = _provider(gemini_settings, handler)
    await provider.embed_documents([EmbeddingInput("chunk text", title="Runners")])
    await provider.embed_query("how to register")
    doc_req, query_req = seen
    assert doc_req.url.path == "/v1beta/models/gemini-embedding-001:batchEmbedContents"
    assert doc_req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(doc_req.url)
    [item] = json.loads(doc_req.content)["requests"]
    assert item == {
        "model": "models/gemini-embedding-001",
        "content": {"parts": [{"text": "chunk text"}]},
        "taskType": "RETRIEVAL_DOCUMENT",
        "outputDimensionality": DIM,
        "title": "Runners",
    }
    [q] = json.loads(query_req.content)["requests"]
    assert q["taskType"] == "RETRIEVAL_QUERY" and "title" not in q
    assert provider.spec == "gemini-embedding-001@4"


async def test_batches_preserve_order_and_normalise(gemini_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
        return httpx.Response(
            200, json={"embeddings": [{"values": [float(t)] + [0.0] * (DIM - 1)} for t in texts]}
        )

    provider, _ = _provider(gemini_settings, handler)
    vectors = await provider.embed_documents([EmbeddingInput(str(i)) for i in range(1, 6)])
    assert provider.requests_made == 3  # batch size 2 → 2 + 2 + 1
    assert [v[0] for v in vectors] == [1.0] * 5  # each normalised to unit length
    assert all(math.isclose(sum(x * x for x in v), 1.0) for v in vectors)


async def test_wrong_dimension_is_rejected(gemini_settings: Settings) -> None:
    provider, _ = _provider(
        gemini_settings,
        lambda r: httpx.Response(200, json={"embeddings": [{"values": [1.0] * 3072}]}),
    )
    with pytest.raises(EmbeddingProviderError, match="wrong dimension"):
        await provider.embed_query("x")


async def test_wrong_count_is_rejected(gemini_settings: Settings) -> None:
    provider, _ = _provider(gemini_settings, lambda r: httpx.Response(200, json={"embeddings": []}))
    with pytest.raises(EmbeddingProviderError, match="wrong number"):
        await provider.embed_query("x")


async def test_malformed_response_is_rejected(gemini_settings: Settings) -> None:
    provider, _ = _provider(gemini_settings, lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(EmbeddingProviderError, match="unexpected response"):
        await provider.embed_query("x")


async def test_rate_limit_is_retried_with_retry_after(gemini_settings: Settings) -> None:
    responses = iter([httpx.Response(429, headers={"retry-after": "3"}), None])

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        return response if response is not None else _ok(request)

    provider, sleeps = _provider(gemini_settings, handler)
    assert len(await provider.embed_query("x")) == DIM
    assert 3.0 in sleeps
    assert provider.requests_made == 2


async def test_rate_limit_honours_google_retry_info_body(gemini_settings: Settings) -> None:
    quota = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}],
        }
    }
    responses = iter([httpx.Response(429, json=quota), None])

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        return response if response is not None else _ok(request)

    provider, sleeps = _provider(gemini_settings, handler)
    await provider.embed_query("x")
    assert 37.0 in sleeps


async def test_daily_quota_fails_fast_without_retrying(gemini_settings: Settings) -> None:
    daily = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"}
                    ],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"},
            ],
        }
    }
    provider, sleeps = _provider(gemini_settings, lambda r: httpx.Response(429, json=daily))
    with pytest.raises(EmbeddingQuotaExhaustedError) as info:
        await provider.embed_query("x")
    assert provider.requests_made == 1 and sleeps == []
    assert info.value.details and "PerDay" in info.value.details["quota"][0]


async def test_retries_are_bounded(gemini_settings: Settings) -> None:
    provider, _ = _provider(gemini_settings, lambda r: httpx.Response(503))
    with pytest.raises(EmbeddingProviderError) as info:
        await provider.embed_documents([EmbeddingInput("x")])
    assert provider.requests_made == 3  # 1 + embedding_max_retries
    assert info.value.details and info.value.details["status"] == 503


async def test_client_errors_are_not_retried_and_are_redacted(gemini_settings: Settings) -> None:
    body = {
        "error": {
            "status": "INVALID_ARGUMENT",
            "message": "API key sk-ABCDEFGHIJKLMNOPQRSTUV not valid",
        }
    }
    provider, _ = _provider(gemini_settings, lambda r: httpx.Response(400, json=body))
    with pytest.raises(EmbeddingProviderError) as info:
        await provider.embed_query("x")
    assert provider.requests_made == 1
    assert info.value.details is not None
    assert info.value.details["provider_status"] == "INVALID_ARGUMENT"
    assert "sk-ABCDEF" not in info.value.details["provider_message"]


async def test_network_errors_are_retried_then_raised(gemini_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    provider, _ = _provider(gemini_settings, handler)
    with pytest.raises(EmbeddingProviderError, match="Could not reach"):
        await provider.embed_documents([EmbeddingInput("x")])
    assert provider.requests_made == 3


def test_missing_api_key_fails_fast(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(EmbeddingProviderError, match="EMBEDDING_API_KEY"):
        GeminiEmbeddingProvider(make_settings(embedding_provider="gemini", embedding_api_key=None))


def test_zero_vector_is_rejected() -> None:
    with pytest.raises(EmbeddingProviderError):
        l2_normalize([0.0, 0.0])
