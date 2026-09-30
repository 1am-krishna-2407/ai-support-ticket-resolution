"""Write the OpenAPI snapshot (docs/openapi.json) and the route reference (docs/api.md).

    python -m app.scripts.export_openapi [--docs-dir ../docs] [--check]

The spec is built from the application itself with fixed, credential-free settings, so
the output does not depend on the local .env. `--check` exits 1 when the committed files
are out of date (the unit test suite runs the same comparison).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import Settings

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


def build_spec() -> dict[str, Any]:
    from app.main import create_app

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:unused@localhost:5432/unused",
        admin_mode="dev",
        environment="development",
    )
    return create_app(settings).openapi()


def render_markdown(spec: dict[str, Any]) -> str:
    lines = [
        "# API reference",
        "",
        "Generated from the application by `python -m app.scripts.export_openapi`; do not edit",
        "by hand. The machine-readable spec is [`openapi.json`](openapi.json); a running backend",
        "also serves it at `/api/v1/openapi.json` with interactive docs at `/api/v1/docs`.",
        "",
        'Every error uses one contract: `{"error": {"code", "message", "details",',
        '"request_id"}}`. Stack traces and submitted input values are never returned. Every',
        "response carries an `X-Request-ID` header.",
        "",
        "| Method | Path | Summary | Success | Documented errors | Admin |",
        "|---|---|---|---|---|---|",
    ]
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            codes = sorted(op.get("responses", {}), key=str)
            success = [c for c in codes if str(c).startswith("2")]
            failures = [c for c in codes if not str(c).startswith("2")]
            admin = any(p.get("name") == "X-Admin-Token" for p in op.get("parameters", []))
            summary = str(op.get("summary", "")).replace("|", "\\|")
            lines.append(
                f"| {method.upper()} | `{path}` | {summary} | {', '.join(success)} "
                f"| {', '.join(failures) or '-'} | {'yes' if admin else ''} |"
            )
    lines += ["", "## Error codes", "", "| Status | Meaning |", "|---|---|"]
    from app.api.responses import _DESCRIPTIONS

    for status, text in sorted(_DESCRIPTIONS.items()):
        lines.append(f"| {status} | {text} |")
    lines.append(
        "| 500 | Unexpected failure (`INTERNAL_ERROR`); details are logged, not returned. |"
    )
    return "\n".join(lines) + "\n"


def render(spec: dict[str, Any]) -> dict[str, str]:
    return {
        "openapi.json": json.dumps(spec, indent=2, sort_keys=True) + "\n",
        "api.md": render_markdown(spec),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for name, content in render(build_spec()).items():
        target = args.docs_dir / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(name)
        else:
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {target}")
    if stale:
        print(f"out of date: {', '.join(stale)}; run python -m app.scripts.export_openapi")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
