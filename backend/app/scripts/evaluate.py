"""Run an evaluation over evaluation/cases.json and store the results.

    python -m app.scripts.evaluate [--name NAME] [--method hybrid|vector|lexical]
                                   [--top-k N] [--k 5] [--generation] [--limit N]
                                   [--types TYPE ...]

Retrieval-only by default (no LLM calls). `--generation` also runs triage and draft
generation for every case (two LLM calls per case). Prints the stored run as JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.main import build_embedder, build_llm
from app.schemas.retrieval import RetrievalMethod
from app.scripts._offline import offline_settings
from app.services.analysis.prompts import PromptRegistry
from app.services.evaluation.dataset import load_dataset
from app.services.evaluation.factory import build_evaluation_runner
from app.services.evaluation.runner import EvaluationOptions, get_run


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate retrieval (and optionally generation).")
    parser.add_argument("--name")
    parser.add_argument("--method", choices=[m.value for m in RetrievalMethod], default="hybrid")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--generation", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--types", nargs="+", default=[])
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse(argv)
    settings = offline_settings(get_settings())
    configure_logging("WARNING", sys.stderr)  # keep stdout for the JSON report
    dataset = load_dataset(Path(settings.evaluation_dataset_path))
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    embedder = build_embedder(settings)
    llm = build_llm(settings) if args.generation else None
    try:
        runner = build_evaluation_runner(
            settings,
            engine=engine,
            session_factory=sessions,
            embedder=embedder,
            llm=llm,
            prompts=PromptRegistry(),
        )
        options = EvaluationOptions(
            name=args.name,
            method=RetrievalMethod(args.method),
            top_k=args.top_k,
            k=args.k,
            generation=args.generation,
            limit=args.limit,
            case_types=tuple(args.types),
        )
        run_id = await runner.run(options, dataset)
        async with sessions() as session:
            found = await get_run(session, run_id)
            if found is None:
                raise AppError("The evaluation run was not stored.")
            run, _ = found
            summary = {
                "id": str(run.id),
                "status": run.status,
                "config": run.config,
                "metrics": run.metrics,
            }
            print(json.dumps(summary, indent=2, default=str))
    except AppError as exc:
        print(json.dumps({"status": "failed", "error": exc.message}), file=sys.stderr)
        return 2
    finally:
        if embedder:
            await embedder.aclose()
        if llm:
            await llm.aclose()
        await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
