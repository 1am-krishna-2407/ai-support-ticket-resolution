from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.categories import Category
from app.services.ingestion.manifest import ManifestError, load_manifest, parse_manifest

ALLOWED = ["docs.gitlab.com"]
REPO_MANIFEST = Path(__file__).resolve().parents[3] / "knowledge" / "manifest" / "sources.json"


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "document_id": "ci-runners",
        "title": "Runners",
        "canonical_url": "https://docs.gitlab.com/ci/runners/",
        "category": "runners",
        "source_type": "product_documentation",
        "license": "CC BY-SA 4.0",
        "source_version": None,
        "enabled": True,
    }
    entry.update(overrides)
    return entry


def test_valid_manifest() -> None:
    [entry] = parse_manifest([_entry()], ALLOWED)
    assert entry.category is Category.RUNNERS


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({}, "JSON array"),
        ([], "no entries"),
        ([_entry(canonical_url="http://docs.gitlab.com/x/")], "https"),
        ([_entry(canonical_url="https://evil.com/x/")], "allowlist"),
        ([_entry(category="billing")], "category"),
        ([_entry(document_id="Has Spaces")], "document_id"),
        ([_entry(license="")], "license"),
        ([_entry(unexpected="x")], "unexpected"),
        ([_entry(), _entry()], "duplicate document_id"),
        (
            [_entry(), _entry(document_id="other")],
            "duplicate canonical_url",
        ),
    ],
)
def test_invalid_manifests(data: object, problem: str) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(data, ALLOWED)


def test_all_problems_are_reported() -> None:
    with pytest.raises(ManifestError) as info:
        parse_manifest([_entry(category="nope"), _entry(canonical_url="http://x/")], ALLOWED)
    assert len(info.value.problems) == 2


def test_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path / "missing.json", ALLOWED)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(bad, ALLOWED)


def test_repository_manifest_is_valid_and_covers_every_category() -> None:
    entries = load_manifest(REPO_MANIFEST, ALLOWED)
    assert 150 <= len(entries) <= 300
    assert {e.category for e in entries} == set(Category)
    assert all(e.license == "CC BY-SA 4.0" for e in entries)
    raw = json.loads(REPO_MANIFEST.read_text(encoding="utf-8"))
    # retrieved_at / content_hash are pipeline outputs and must never be hand-written.
    assert not any("retrieved_at" in e or "content_hash" in e for e in raw)
