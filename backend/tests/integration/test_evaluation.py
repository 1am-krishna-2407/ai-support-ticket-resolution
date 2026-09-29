"""Evaluation runner and API against real Postgres + pgvector (seeded corpus, fake providers)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.errors import EvaluationAlreadyRunningError
from app.db.session import create_session_factory
from app.main import create_app
from app.schemas.retrieval import RetrievalMethod
from app.services.analysis.prompts import PromptRegistry
from app.services.evaluation.dataset import EvaluationDataset
from app.services.evaluation.factory import build_evaluation_runner
from app.services.evaluation.runner import EvaluationOptions, evaluation_lock, get_run
from tests.fakes import FakeLLMProvider
from tests.integration.test_retrieval import QUERY_VECTORS, MappingEmbedder, corpus  # noqa: F401
from tests.unit.test_ticket_analysis import triage_json

pytestmark = pytest.mark.integration
ADMIN = {"X-Admin-Token": "test-admin-token-0123456789"}

DATASET = {
    "description": "test",
    "version": "t-1",
    "cases": [
        {"id": "e1", "type": "runner_registration", "title": "Register a runner",
         "description": "How do I register a runner with a token?", "expected_category": "runners",
         "expected_priority": "low", "expected_sources": ["runner-register"],
         "expected_behavior": "answer"},
        {"id": "e2", "type": "ci_variables", "title": "Masked variable question",
         "description": "masked variable values appear in job logs", "expected_category": "ci_cd",
         "expected_priority": "low", "expected_sources": ["ci-variables"],
         "expected_behavior": "answer"},
        {"id": "e3", "type": "out_of_scope", "title": "Banana smoothie",
         "description": "How do I make a banana smoothie?", "expected_category": None,
         "expected_priority": None, "expected_sources": [],
         "expected_behavior": "insufficient_evidence"},
    ],
}  # fmt: skip


def resolution(source: str = "SRC-001") -> str:
    return json.dumps(
        {
            "answer_type": "resolution", "summary": "Register the runner.",
            "resolution_steps": [{"instruction": "Run `gitlab-runner register`.", "source_ids": [source]}],
            "explanation": "e", "warnings": [], "questions_for_customer": [],
            "source_ids": [source], "confidence": "medium", "grounded": True,
        }
    )  # fmt: skip


def _runner(settings: Settings, engine: AsyncEngine, llm: Any = None):  # type: ignore[no-untyped-def]
    return build_evaluation_runner(
        settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        embedder=MappingEmbedder(QUERY_VECTORS),  # type: ignore[arg-type]
        llm=llm,
        prompts=PromptRegistry(),
    )


async def test_retrieval_only_run_is_stored_with_frozen_config(corpus: Any) -> None:  # noqa: F811
    settings, engine = corpus
    dataset = EvaluationDataset.model_validate(DATASET)
    runner = _runner(settings, engine)
    run_id = await runner.run(EvaluationOptions(name="baseline", k=3), dataset)

    async with create_session_factory(engine)() as session:
        found = await get_run(session, run_id)
    assert found is not None
    run, cases = found
    assert run.status == "completed" and run.completed_cases == 3
    assert run.config["dataset_version"] == "t-1" and run.config["method"] == "hybrid"
    assert run.config["generation"] is False and run.config["embedding_spec"] == "fake-embedding@8"
    d = run.metrics["deterministic"]
    assert d["hit_rate@3"] == 1.0  # both answerable queries find their document
    assert d["correct_refusal_rate"] == 1.0  # banana query is refused
    assert set(run.metrics["by_type"]) == {"runner_registration", "ci_variables", "out_of_scope"}
    by_id = {c.case_id: c for c in cases}
    assert by_id["e1"].passed and by_id["e3"].passed
    assert by_id["e1"].actual["retrieval"]["documents"][0] == "runner-register"


async def test_generation_run_scores_answers(corpus: Any) -> None:  # noqa: F811
    settings, engine = corpus
    dataset = EvaluationDataset.model_validate({**DATASET, "cases": DATASET["cases"][:1]})
    llm = FakeLLMProvider({"triage": [triage_json()], "resolution": [resolution()]})
    run_id = await _runner(settings, engine, llm).run(
        EvaluationOptions(generation=True, k=3), dataset
    )
    async with create_session_factory(engine)() as session:
        found = await get_run(session, run_id)
    assert found is not None
    run, [case] = found
    d = run.metrics["deterministic"]
    assert d["category_accuracy"] == 1.0 and d["priority_exact"] == 1.0
    assert d["grounded_rate"] == 1.0 and d["schema_validity_rate"] == 1.0
    assert d["citation_precision"] == 1.0
    assert case.actual["analysis"]["status"] == "draft" and case.passed
    assert llm.calls_for("resolution") == 1  # retrieval reused, not repeated
    assert run.config["llm_model"] == "fake-llm" and run.config["prompt_versions"]


async def test_method_comparison_and_case_filters(corpus: Any) -> None:  # noqa: F811
    settings, engine = corpus
    dataset = EvaluationDataset.model_validate(DATASET)
    runner = _runner(settings, engine)
    lexical = await runner.run(
        EvaluationOptions(method=RetrievalMethod.LEXICAL, case_types=("ci_variables",)), dataset
    )
    async with create_session_factory(engine)() as session:
        found = await get_run(session, lexical)
    assert found is not None
    run, cases = found
    assert [c.case_id for c in cases] == ["e2"] and run.config["method"] == "lexical"


async def test_single_flight(corpus: Any) -> None:  # noqa: F811
    settings, engine = corpus
    dataset = EvaluationDataset.model_validate(DATASET)
    async with evaluation_lock(engine):
        with pytest.raises(EvaluationAlreadyRunningError):
            await _runner(settings, engine).run(EvaluationOptions(), dataset)


# --- API --------------------------------------------------------------------------------------


@pytest.fixture
async def api(
    corpus: tuple[Settings, AsyncEngine],  # noqa: F811
    tmp_path: Path,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings, _ = corpus
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(DATASET), encoding="utf-8")
    app = create_app(settings.model_copy(update={"evaluation_dataset_path": str(path)}))
    async with app.router.lifespan_context(app):
        app.state.embedder = MappingEmbedder(QUERY_VECTORS)
        transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield app, c


async def _wait(c: httpx.AsyncClient) -> None:
    for _ in range(200):
        if not (await c.get("/api/v1/evaluations")).json()["running"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("evaluation did not finish")


async def test_evaluation_api_lifecycle(api: Any) -> None:
    _, c = api
    assert (await c.post("/api/v1/evaluations/run", json={})).status_code == 401
    r = await c.post("/api/v1/evaluations/run", json={"name": "api run", "k": 3}, headers=ADMIN)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    run_id = r.json()["id"]
    await _wait(c)
    listing = (await c.get("/api/v1/evaluations")).json()
    assert listing["items"][0]["id"] == run_id and listing["items"][0]["status"] == "completed"
    detail = (await c.get(f"/api/v1/evaluations/{run_id}")).json()
    assert len(detail["cases"]) == 3 and detail["metrics"]["deterministic"]["hit_rate@3"] == 1.0


async def test_evaluation_api_validation(api: Any) -> None:
    _, c = api
    r = await c.post("/api/v1/evaluations/run", json={"case_types": ["nope"]}, headers=ADMIN)
    assert r.status_code == 400 and r.json()["error"]["details"]["case_types"] == ["nope"]
    r = await c.post("/api/v1/evaluations/run", json={"generation": True}, headers=ADMIN)
    assert r.status_code == 502 and r.json()["error"]["code"] == "LLM_PROVIDER_ERROR"
    assert (
        await c.post("/api/v1/evaluations/run", json={"k": 0}, headers=ADMIN)
    ).status_code == 422
    missing = await c.get("/api/v1/evaluations/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


async def test_degraded_retrieval_is_an_error_not_a_refusal(corpus: Any) -> None:  # noqa: F811
    from app.core.errors import EmbeddingQuotaExhaustedError

    settings, engine = corpus

    class Outage(MappingEmbedder):
        async def embed_query(self, text: str) -> list[float]:
            raise EmbeddingQuotaExhaustedError()

    runner = build_evaluation_runner(
        settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        embedder=Outage({}),  # type: ignore[arg-type]
        llm=None,
        prompts=PromptRegistry(),
    )
    run_id = await runner.run(EvaluationOptions(k=3), EvaluationDataset.model_validate(DATASET))
    async with create_session_factory(engine)() as session:
        found = await get_run(session, run_id)
    assert found is not None
    run, cases = found
    assert run.metrics["errors"] == 3 and run.metrics["scored_cases"] == 0
    assert all(c.error and "without embeddings" in c.error for c in cases)
    assert run.metrics["deterministic"]["false_refusal_rate"] is None
