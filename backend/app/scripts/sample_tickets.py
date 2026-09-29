"""Run sample tickets through the full analysis pipeline with the configured providers.

    python -m app.scripts.sample_tickets [--path evaluation/sample_tickets.json]

Prints a JSON summary per ticket: category, priority, resolution status, grounding,
cited sources, review reasons, errors, and timings. Ticket text and model output are
not logged; only this summary is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.tokenizer import get_tokenizer
from app.db.session import create_engine, create_session_factory
from app.main import build_embedder, build_llm
from app.schemas.tickets import TicketInput
from app.scripts._offline import offline_settings
from app.services.analysis.prompts import PromptRegistry
from app.services.analysis.service import TicketAnalysisService
from app.services.retrieval.service import RetrievalService


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="evaluation/sample_tickets.json")
    args = parser.parse_args(argv)
    settings = offline_settings(get_settings())
    configure_logging("WARNING", sys.stderr)  # keep stdout for the JSON report
    data = json.loads(await asyncio.to_thread(Path(args.path).read_text, encoding="utf-8"))

    engine = create_engine(settings)
    embedder = build_embedder(settings)
    llm = build_llm(settings)
    service = TicketAnalysisService(
        settings=settings,
        llm=llm,
        retrieval=RetrievalService(
            settings=settings, session_factory=create_session_factory(engine), embedder=embedder
        ),
        prompts=PromptRegistry(),
        count_tokens=get_tokenizer(settings.tokenizer_encoding).count,
        embedding_spec=embedder.spec if embedder else None,
    )
    results: list[dict[str, Any]] = []
    try:
        for item in data["tickets"]:
            ticket = TicketInput(
                title=item["title"],
                description=item["description"],
                environment=item.get("environment"),
            )
            out = await service.analyze(ticket)
            results.append(
                {
                    "id": item["id"],
                    "category": out.classification.category,
                    "confidence": out.classification.confidence,
                    "priority": out.priority.level,
                    "status": out.resolution.status,
                    "steps": len(out.resolution.steps),
                    "grounded": out.grounding.grounded,
                    "grounding_score": out.grounding.score,
                    "failed_checks": [
                        f"{c.name}: {c.detail}" for c in out.grounding.checks if not c.passed
                    ],
                    "sources": [
                        f"{s.source_id} {s.title} ({s.final_score:.2f})" for s in out.sources
                    ],
                    "evidence_sufficient": out.retrieval.evidence_sufficient,
                    "review": [r.value for r in out.human_review.reasons],
                    "errors": [f"{e.stage}:{e.code}" for e in out.errors],
                    "timings_ms": out.timings_ms,
                    "summary": out.resolution.summary[:300],
                    "first_step": out.resolution.steps[0].instruction[:300]
                    if out.resolution.steps
                    else None,
                }
            )
    finally:
        if embedder:
            await embedder.aclose()
        if llm:
            await llm.aclose()
        await engine.dispose()
    print(json.dumps({"dataset_version": data["version"], "results": results}, indent=2))
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
