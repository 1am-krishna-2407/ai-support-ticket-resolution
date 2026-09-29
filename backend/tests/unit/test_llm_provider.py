from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import InvalidModelOutputError, LLMProviderError, LLMTimeoutError
from app.providers.llm import ChatMessage, OpenAICompatibleChatProvider, strict_json_schema
from app.schemas.analysis import GeneratedResolution, TriageOutput

KEY = "gsk_test_key_0000000000"
MESSAGES = [ChatMessage("system", "rules"), ChatMessage("user", "ticket")]


@pytest.fixture
def llm_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(
        llm_api_key=KEY,
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model="openai/gpt-oss-120b",
        llm_max_retries=2,
        llm_seed=7,
    )


def ok(content: str = '{"a": 1}', finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "openai/gpt-oss-120b",
            "choices": [
                {"message": {"content": content, "reasoning": "hidden"}, "finish_reason": finish}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def provider(settings: Settings, handler: Callable[[httpx.Request], httpx.Response]):  # type: ignore[no-untyped-def]
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    return OpenAICompatibleChatProvider(
        settings, transport=httpx.MockTransport(handler), sleep=sleep
    ), sleeps


async def _call(p: OpenAICompatibleChatProvider) -> Any:
    return await p.complete_json(MESSAGES, schema_name="triage", schema={"type": "object"})


async def test_request_body_and_auth(llm_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok()

    p, _ = provider(llm_settings, handler)
    result = await _call(p)
    [request] = seen
    assert request.url == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["temperature"] == 0.0 and body["seed"] == 7
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "ticket"},
    ]
    assert result.content == '{"a": 1}' and result.prompt_tokens == 10
    assert "hidden" not in repr(result)  # reasoning text is never surfaced


async def test_rate_limit_retry_honours_retry_after(llm_settings: Settings) -> None:
    responses = iter([httpx.Response(429, headers={"retry-after": "4"}), ok()])
    p, sleeps = provider(llm_settings, lambda r: next(responses))
    await _call(p)
    assert sleeps == [4.0]


async def test_retries_are_bounded(llm_settings: Settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    p, _ = provider(llm_settings, handler)
    with pytest.raises(LLMProviderError) as info:
        await _call(p)
    assert calls == 3 and info.value.details and info.value.details["status"] == 503


async def test_timeout_maps_to_llm_timeout(llm_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    p, _ = provider(llm_settings, handler)
    with pytest.raises(LLMTimeoutError):
        await _call(p)


async def test_client_error_not_retried_and_redacted(llm_settings: Settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"error": {"code": "invalid_request_error", "message": "Bearer abc.def failed"}},
        )

    p, _ = provider(llm_settings, handler)
    with pytest.raises(LLMProviderError) as info:
        await _call(p)
    assert calls == 1
    assert info.value.details and "abc.def" not in info.value.details["provider_message"]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (ok(finish="length"), InvalidModelOutputError),
        (ok(content="  "), InvalidModelOutputError),
        (httpx.Response(200, json={"unexpected": True}), LLMProviderError),
    ],
)
async def test_bad_responses(
    llm_settings: Settings, response: httpx.Response, error: type[Exception]
) -> None:
    p, _ = provider(llm_settings, lambda r: response)
    with pytest.raises(error):
        await _call(p)


def test_missing_key_fails_fast(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(LLMProviderError, match="LLM_API_KEY"):
        OpenAICompatibleChatProvider(make_settings(llm_api_key=None))


def _objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_objects(value))
    return found


@pytest.mark.parametrize("model", [TriageOutput, GeneratedResolution])
def test_strict_schema_rules(model: Any) -> None:
    schema = strict_json_schema(model)
    text = json.dumps(schema)
    assert "$ref" not in text and '"title"' not in text and '"default"' not in text
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


async def test_token_bucket_paces_to_tokens_per_minute() -> None:
    from app.providers.llm import TokenBucket

    now = [0.0]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(6000, clock=lambda: now[0], sleep=sleep)  # 100 tokens/s
    assert await bucket.acquire(5000) == 0  # full bucket: no wait
    waited = await bucket.acquire(3000)  # 1000 left, needs 2000 more: 20 s
    assert waited == pytest.approx(20.0)
    assert await bucket.acquire(99_999) == pytest.approx(60.0)  # capped at full capacity


async def test_requests_reserve_prompt_plus_output_tokens(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(
        llm_api_key=KEY, llm_base_url="https://api.groq.com/openai/v1",
        llm_tokens_per_minute=1200, llm_max_output_tokens=1000,
    )  # fmt: skip
    now = [0.0]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    p = OpenAICompatibleChatProvider(
        settings, transport=httpx.MockTransport(lambda r: ok()), sleep=sleep, clock=lambda: now[0]
    )
    await _call(p)  # ~1000+ tokens reserved from a 1200 budget
    await _call(p)  # must wait for the budget to refill
    assert sleeps and sum(sleeps) > 30


async def test_schema_validation_failure_is_invalid_model_output(llm_settings: Settings) -> None:
    body = {"error": {"code": "json_validate_failed", "message": "Failed to validate JSON"}}
    p, _ = provider(llm_settings, lambda r: httpx.Response(400, json=body))
    with pytest.raises(InvalidModelOutputError) as info:
        await _call(p)
    assert info.value.details and info.value.details["provider_code"] == "json_validate_failed"
