from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.config import EmbeddingProviderName, Settings
from app.core.errors import EmbeddingProviderError, EmbeddingQuotaExhaustedError
from app.providers.embeddings import (
    EmbeddingInput,
    GeminiEmbeddingProvider,
    HuggingFaceEmbeddingProvider,
    create_embedding_provider,
)

DIM = 4
HF_TOKEN = "hf_test_token_000000000000"
INSTRUCTION = "Retrieve docs for the ticket"


@pytest.fixture
def hf_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(
        embedding_provider="huggingface",
        hf_token=HF_TOKEN,
        embedding_api_key="some-other-providers-key",  # must NOT be used for Hugging Face
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_dimension=DIM,
        embedding_batch_size=2,
        embedding_max_retries=2,
        embedding_max_requests_per_minute=600,
        embedding_query_instruction=INSTRUCTION,
    )


def ok(request: httpx.Request) -> httpx.Response:
    inputs = json.loads(request.content)["input"]
    # Return data out of order to check that results are re-sorted by index.
    data = [
        {"index": i, "embedding": [float(i + 1)] + [0.0] * (DIM - 1)} for i in range(len(inputs))
    ]
    return httpx.Response(
        200, json={"data": list(reversed(data)), "model": "Qwen/Qwen3-Embedding-0.6B"}
    )


def provider(settings: Settings, handler: Callable[[httpx.Request], httpx.Response]):  # type: ignore[no-untyped-def]
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    p = HuggingFaceEmbeddingProvider(settings, transport=httpx.MockTransport(handler), sleep=sleep)
    return p, sleeps


async def test_request_shape_auth_and_default_route(hf_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(request)

    p, _ = provider(hf_settings, handler)
    await p.embed_documents([EmbeddingInput("chunk text", title="Runners")])
    [request] = seen
    assert str(request.url) == "https://router.huggingface.co/deepinfra/v1/openai/embeddings"
    assert request.headers["authorization"] == f"Bearer {HF_TOKEN}"
    body = json.loads(request.content)
    assert body == {
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "input": ["chunk text"],  # documents carry no instruction
        "dimensions": DIM,
        "encoding_format": "float",
    }
    assert p.spec == "Qwen/Qwen3-Embedding-0.6B@4"


async def test_queries_get_the_qwen_instruction_prefix(hf_settings: Settings) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return ok(request)

    p, _ = provider(hf_settings, handler)
    await p.embed_query("runner won't register")
    assert seen[0]["input"] == [f"Instruct: {INSTRUCTION}\nQuery:runner won't register"]


async def test_batches_preserve_order_and_normalise(hf_settings: Settings) -> None:
    p, _ = provider(hf_settings, ok)
    vectors = await p.embed_documents([EmbeddingInput(str(i)) for i in range(5)])
    assert p.requests_made == 3  # batch size 2
    # Within each batch index 0 → value 1, index 1 → value 2 (before normalisation all
    # vectors point along the first axis), so every vector normalises to [1, 0, 0, 0].
    assert all(v == [1.0, 0.0, 0.0, 0.0] for v in vectors)
    assert all(math.isclose(sum(x * x for x in v), 1.0) for v in vectors)


async def test_out_of_order_response_is_resorted_by_index(hf_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [
                {"index": 1, "embedding": [0.0, 1.0, 0.0, 0.0]},
                {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
            ]},
        )  # fmt: skip

    p, _ = provider(hf_settings, handler)
    first, second = await p.embed_documents([EmbeddingInput("a"), EmbeddingInput("b")])
    assert first[0] == 1.0 and second[1] == 1.0


async def test_persistent_payment_required_means_credits_exhausted(hf_settings: Settings) -> None:
    p, sleeps = provider(
        hf_settings, lambda r: httpx.Response(402, json={"error": "You have exceeded your credits"})
    )
    with pytest.raises(EmbeddingQuotaExhaustedError) as info:
        await p.embed_documents([EmbeddingInput("x")])
    backoffs = [s for s in sleeps if s >= 1.0]  # excludes 0.1 s request pacing
    assert p.requests_made == 3 and len(backoffs) == 2  # retried before giving up
    assert info.value.details and info.value.details["quota"] == ["huggingface-credits"]


async def test_transient_payment_required_is_retried(hf_settings: Settings) -> None:
    responses = iter([httpx.Response(402, json={"error": "Payment Required"}), None])

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        return response if response is not None else ok(request)

    p, _ = provider(hf_settings, handler)
    assert len(await p.embed_query("x")) == DIM
    assert p.requests_made == 2


async def test_rate_limit_and_model_loading_are_retried(hf_settings: Settings) -> None:
    responses = iter([httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(503), None])

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        return response if response is not None else ok(request)

    p, sleeps = provider(hf_settings, handler)
    assert len((await p.embed_documents([EmbeddingInput("x")]))[0]) == DIM
    assert sleeps[0] == 2.0 and p.requests_made == 3


async def test_string_error_bodies_are_reported_and_redacted(hf_settings: Settings) -> None:
    p, _ = provider(
        hf_settings,
        lambda r: httpx.Response(400, json={"error": "bad input for Bearer hf_secretvalue123"}),
    )
    with pytest.raises(EmbeddingProviderError) as info:
        await p.embed_query("x")
    assert info.value.details is not None
    assert "hf_secretvalue123" not in info.value.details["provider_message"]


async def test_wrong_dimension_is_rejected(hf_settings: Settings) -> None:
    p, _ = provider(
        hf_settings,
        lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 1024}]}),
    )
    with pytest.raises(EmbeddingProviderError, match="wrong dimension"):
        await p.embed_query("x")


def test_missing_token_fails_fast(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(EmbeddingProviderError, match="HF_TOKEN"):
        HuggingFaceEmbeddingProvider(make_settings(embedding_provider="huggingface"))


def test_factory_selects_provider(make_settings: Callable[..., Settings]) -> None:
    hf = create_embedding_provider(make_settings(hf_token=HF_TOKEN))
    gemini = create_embedding_provider(
        make_settings(
            embedding_provider="gemini", embedding_api_key="g-key", embedding_batch_size=50
        )
    )
    assert isinstance(hf, HuggingFaceEmbeddingProvider)
    assert isinstance(gemini, GeminiEmbeddingProvider)


def test_settings_resolve_keys_and_urls(make_settings: Callable[..., Settings]) -> None:
    s = make_settings(hf_token=HF_TOKEN, embedding_api_key="gemini-key")
    assert s.embedding_provider is EmbeddingProviderName.HUGGINGFACE
    assert s.resolved_embedding_api_key is not None
    assert s.resolved_embedding_api_key.get_secret_value() == HF_TOKEN
    g = make_settings(
        embedding_provider="gemini", embedding_api_key="gemini-key", embedding_batch_size=50
    )
    assert g.resolved_embedding_api_key is not None
    assert g.resolved_embedding_api_key.get_secret_value() == "gemini-key"
    assert g.resolved_embedding_base_url.startswith("https://generativelanguage.googleapis.com")
    custom = make_settings(embedding_base_url="https://example.test/v1")
    assert custom.resolved_embedding_base_url == "https://example.test/v1"


def test_gemini_batch_limit_is_enforced(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValueError, match="EMBEDDING_BATCH_SIZE"):
        make_settings(embedding_provider="gemini", embedding_batch_size=101)


async def test_circuit_opens_after_exhausted_credits(hf_settings: Settings) -> None:
    p, _ = provider(hf_settings, lambda r: httpx.Response(402, json={"error": "depleted"}))
    with pytest.raises(EmbeddingQuotaExhaustedError):
        await p.embed_query("x")
    made = p.requests_made
    with pytest.raises(EmbeddingQuotaExhaustedError) as info:
        await p.embed_query("y")
    assert p.requests_made == made  # no request while the circuit is open
    assert info.value.details and info.value.details["circuit_open"] is True


async def test_circuit_closes_after_cooldown(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(
        hf_token=HF_TOKEN, embedding_dimension=DIM, embedding_max_retries=0,
        embedding_query_max_retries=0,
        embedding_quota_cooldown_seconds=0, embedding_max_requests_per_minute=600,
    )  # fmt: skip
    responses = iter([httpx.Response(402, json={"error": "depleted"}), None])

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        return response if response is not None else ok(request)

    p, _ = provider(settings, handler)
    with pytest.raises(EmbeddingQuotaExhaustedError):
        await p.embed_query("x")
    assert len(await p.embed_query("y")) == DIM  # cooldown 0: tries again and succeeds
    assert p.last_failure is None


async def test_queries_retry_at_most_once(hf_settings: Settings) -> None:
    p, _ = provider(hf_settings, lambda r: httpx.Response(503))
    with pytest.raises(EmbeddingProviderError):
        await p.embed_query("x")
    assert p.requests_made == 2  # interactive path: one retry, then degrade


def test_offline_settings_raise_query_retries_and_disable_cooldown(
    make_settings: Callable[..., Settings],
) -> None:
    from app.scripts._offline import offline_settings

    s = offline_settings(make_settings(embedding_max_retries=5))
    assert s.embedding_query_max_retries == 5 and s.embedding_quota_cooldown_seconds == 0
