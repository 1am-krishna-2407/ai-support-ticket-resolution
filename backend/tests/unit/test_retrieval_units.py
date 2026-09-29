from __future__ import annotations

import pytest

from app.repositories.chunks import ChunkHit
from app.services.retrieval.fusion import order_by_fused_score, reciprocal_rank_fusion
from app.services.retrieval.query import extract_phrases, extract_terms, transform_query
from app.services.retrieval.service import select_diverse


def test_rrf_matches_hand_computed_values() -> None:
    fused = reciprocal_rank_fusion({"a": ["x", "y", "z"], "b": ["y", "w"]}, k=60)
    assert fused["x"] == pytest.approx(1 / 61)
    assert fused["y"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["z"] == pytest.approx(1 / 63)
    assert fused["w"] == pytest.approx(1 / 62)


def test_rrf_weights_and_zero_weight_lists() -> None:
    fused = reciprocal_rank_fusion(
        {"a": ["x"], "b": ["y"], "c": ["z"]}, k=10, weights={"a": 2.0, "b": 0.5, "c": 0.0}
    )
    assert fused == {"x": pytest.approx(2 / 11), "y": pytest.approx(0.5 / 11)}


def test_rrf_duplicates_within_a_list_count_once_at_best_rank() -> None:
    fused = reciprocal_rank_fusion({"a": ["x", "x", "y"]}, k=1)
    assert fused == {"x": pytest.approx(1 / 2), "y": pytest.approx(1 / 3)}


def test_rrf_rejects_invalid_k() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"a": ["x"]}, k=0)


def test_tie_break_by_semantic_then_id() -> None:
    fused = {"b": 0.5, "a": 0.5, "c": 0.5, "d": 0.9}
    assert order_by_fused_score(fused, {"a": 0.2, "b": 0.2, "c": 0.7}) == ["d", "c", "a", "b"]


def test_term_extraction_keeps_identifiers_verbatim() -> None:
    text = (
        "gitlab-runner register fails with x509: certificate signed by unknown authority\n"
        "Using $CI_SERVER_URL and --tls-ca-file=/etc/ssl/ca.pem on GitLab 17.4.1, got HTTP 403."
        " Config in config.toml has clone_url set."
    )
    terms = extract_terms(text)
    assert "CI_SERVER_URL" in terms["ci_variable"]
    assert "--tls-ca-file" in terms["cli_flag"]
    assert "17.4.1" in terms["version"]
    assert "config.toml" in terms["file_name"]
    assert "clone_url" in terms["config_key"]
    assert any(t.startswith("gitlab-runner register") for t in terms["command"])
    assert any("403" in t for t in terms["http_status"])


def test_phrase_extraction_finds_quoted_and_error_lines() -> None:
    phrases = extract_phrases(
        'Pipeline shows "This job is stuck because no runners are online".\n'
        "ERROR: Job failed: exit code 137\nEverything else is fine."
    )
    assert phrases[0] == "This job is stuck because no runners are online"
    assert "ERROR: Job failed: exit code 137" in phrases
    assert not any("Everything else" in p for p in phrases)


def test_transform_query_orders_error_lines_first_and_keeps_terms_when_trimmed() -> None:
    tq = transform_query(
        "Runner cannot register",
        "Some background.\nfatal: unable to access 'https://gitlab.example.com/'\n" + "x " * 500,
        max_chars=200,
    )
    lines = tq.retrieval_query.split("\n")
    assert lines[0] == "Runner cannot register"
    assert lines[1].startswith("fatal: unable to access")
    assert len(tq.retrieval_query) <= 200


def test_transform_query_normalises_unicode_and_control_chars() -> None:
    fullwidth_ci = chr(0xFF23) + chr(0xFF29)
    tq = transform_query(fullwidth_ci + "  job" + chr(0x200B) + " fails", max_chars=100)
    assert tq.retrieval_query == "CI job fails"


def _hit(
    chunk_id: str, document_id: str, content: str, content_hash: str | None = None
) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id, document_id=document_id, document_version=1, chunk_index=0,
        title="T", section=None, subsection=None, heading_path="T", content=content,
        content_hash=content_hash or chunk_id, category="ci_cd",
        canonical_url="https://docs.gitlab.com/t/", anchor=None, token_count=10,
        semantic_score=0.5, lexical_score=0.0,
    )  # fmt: skip


def test_select_diverse_dedupes_and_caps_per_document() -> None:
    ordered = [
        _hit("1", "a", "alpha"),
        _hit("2", "a", "beta"),
        _hit("3", "b", "alpha", content_hash="1"),  # same hash as 1
        _hit("4", "c", "  ALPHA "),  # same normalised text as 1
        _hit("5", "a", "gamma"),
        _hit("6", "b", "delta"),
    ]
    selected = select_diverse(ordered, max_per_document=2)
    assert [h.chunk_id for h in selected] == ["1", "2", "6"]
