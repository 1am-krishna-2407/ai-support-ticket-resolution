"""Source manifest: the curated list of documents to ingest."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.categories import Category
from app.services.ingestion.url_safety import UnsafeUrlError, check_url_syntax


class ManifestError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        preview = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        super().__init__(f"invalid manifest: {preview}{more}")


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,199}$")
    title: str = Field(min_length=1, max_length=500)
    canonical_url: str = Field(min_length=1, max_length=2000)
    category: Category
    source_type: str = Field(min_length=1, max_length=64)
    license: str = Field(min_length=1, max_length=500)
    source_version: str | None = Field(default=None, max_length=100)
    enabled: bool = True


def parse_manifest(data: object, allowed_domains: Iterable[str]) -> list[ManifestEntry]:
    if not isinstance(data, list):
        raise ManifestError(["manifest must be a JSON array"])
    domains = list(allowed_domains)
    problems: list[str] = []
    entries: list[ManifestEntry] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    for index, raw in enumerate(data):
        where = f"entry {index}"
        try:
            entry = ManifestEntry.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(".".join(map(str, e["loc"])) or "entry" for e in exc.errors())
            problems.append(f"{where}: invalid fields ({fields})")
            continue
        where = f"entry {index} ({entry.document_id})"
        try:
            check_url_syntax(entry.canonical_url, domains)
        except UnsafeUrlError as exc:
            problems.append(f"{where}: {exc}")
        if entry.document_id in seen_ids:
            problems.append(f"{where}: duplicate document_id")
        if entry.canonical_url in seen_urls:
            problems.append(f"{where}: duplicate canonical_url")
        seen_ids.add(entry.document_id)
        seen_urls.add(entry.canonical_url)
        entries.append(entry)

    if problems:
        raise ManifestError(problems)
    if not entries:
        raise ManifestError(["manifest contains no entries"])
    return entries


def load_manifest(path: Path, allowed_domains: Iterable[str]) -> list[ManifestEntry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError([f"manifest not found: {path}"]) from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(
            [f"manifest is not valid JSON: {exc.msg} at line {exc.lineno}"]
        ) from exc
    return parse_manifest(data, allowed_domains)
