from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from app.core.tokenizer import get_tokenizer
from app.services.ingestion.chunker import Chunker, ChunkingConfig, make_chunk_id
from app.services.ingestion.parser import (
    CodeBlock,
    GitLabDocsParser,
    Heading,
    ListBlock,
    Paragraph,
    ParsedDocument,
    TableBlock,
    render_block,
)

HTML_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"
PAGES = sorted(p.stem for p in HTML_DIR.glob("*.html"))
count = get_tokenizer("o200k_base").count
DEFAULT = ChunkingConfig(target_tokens=450, overlap_tokens=60, hard_max_tokens=900)


def _doc(*blocks: object, title: str = "Doc") -> ParsedDocument:
    return ParsedDocument(title=title, description=None, blocks=tuple(blocks))  # type: ignore[arg-type]


def _fixture(name: str) -> ParsedDocument:
    html = (HTML_DIR / f"{name}.html").read_text(encoding="utf-8")
    return GitLabDocsParser().parse(html, "https://docs.gitlab.com/x/")


@pytest.mark.parametrize("name", PAGES)
def test_real_pages_respect_limits_and_keep_atomic_blocks(name: str) -> None:
    doc = _fixture(name)
    chunks = Chunker(DEFAULT, count).chunk(doc)
    assert chunks
    assert [c.index for c in chunks] == list(range(len(chunks)))
    joined = "\n\n".join(c.content for c in chunks)
    for block in doc.blocks:
        if (
            isinstance(block, CodeBlock | ListBlock | TableBlock)
            and count(render_block(block)) <= 900
        ):
            # Atomic blocks appear whole inside a single chunk.
            rendered = render_block(block)
            assert any(rendered in c.content for c in chunks), rendered[:80]
        if isinstance(block, Paragraph):
            assert block.text in joined
    for chunk in chunks:
        assert chunk.token_count <= DEFAULT.hard_max_tokens + 50  # + heading-path prefix
        assert chunk.embedding_text.startswith(" > ".join(chunk.heading_path))
        assert chunk.heading_path[0] == doc.title
        assert not chunk.is_split_oversize


def test_chunking_is_deterministic() -> None:
    doc = _fixture("ci_variables")
    first = Chunker(DEFAULT, count).chunk(doc)
    second = Chunker(DEFAULT, count).chunk(doc)
    assert [c.content_hash for c in first] == [c.content_hash for c in second]
    ids = {make_chunk_id("ci-variables", 1, c.index, c.content_hash) for c in first}
    assert len(ids) == len(first)
    assert all(len(i) == 64 for i in ids)


def test_chunk_id_depends_on_every_component() -> None:
    base = make_chunk_id("d", 1, 0, "h")
    assert (
        len(
            {
                base,
                make_chunk_id("e", 1, 0, "h"),
                make_chunk_id("d", 2, 0, "h"),
                make_chunk_id("d", 1, 1, "h"),
                make_chunk_id("d", 1, 0, "i"),
            }
        )
        == 5
    )


def test_chunks_never_cross_headings_and_carry_anchor() -> None:
    doc = _doc(
        Paragraph("Intro."),
        Heading(2, "Install", "install"),
        Paragraph("Install it."),
        Heading(3, "On Linux", "on-linux"),
        Paragraph("Use apt."),
        Heading(2, "Configure", "configure"),
        Paragraph("Edit config.toml."),
    )
    chunks = Chunker(DEFAULT, count).chunk(doc)
    assert [c.heading_path for c in chunks] == [
        ("Doc",),
        ("Doc", "Install"),
        ("Doc", "Install", "On Linux"),
        ("Doc", "Configure"),
    ]
    assert [c.anchor for c in chunks] == [None, "install", "on-linux", "configure"]
    assert chunks[2].section == "Install" and chunks[2].subsection == "On Linux"
    assert chunks[1].content.startswith("## Install\n\nInstall it.")


def test_heading_is_never_emitted_alone() -> None:
    config = ChunkingConfig(target_tokens=40, overlap_tokens=10, hard_max_tokens=200)
    code = CodeBlock("shell", "\n".join(f"echo line {i}" for i in range(20)))
    chunks = Chunker(config, count).chunk(_doc(Heading(2, "Run", "run"), code))
    assert len(chunks) == 1
    assert chunks[0].content.startswith("## Run\n\n```shell")


def test_code_and_procedures_are_not_split_below_hard_max() -> None:
    config = ChunkingConfig(target_tokens=60, overlap_tokens=10, hard_max_tokens=400)
    steps = ListBlock(
        True, tuple(f"Step {i}: do the thing number {i} carefully." for i in range(12))
    )
    code = CodeBlock("yaml", "\n".join(f"key_{i}: value_{i}" for i in range(30)))
    chunks = Chunker(config, count).chunk(_doc(Paragraph("Before."), steps, code))
    assert any(render_block(steps) in c.content for c in chunks)
    assert any(render_block(code) in c.content for c in chunks)
    assert not any(c.is_split_oversize for c in chunks)


def test_oversize_code_is_split_at_line_boundaries_and_flagged() -> None:
    config = ChunkingConfig(target_tokens=50, overlap_tokens=10, hard_max_tokens=100)
    lines = [f"export VARIABLE_{i}=value_{i}" for i in range(80)]
    chunks = Chunker(config, count).chunk(_doc(CodeBlock("shell", "\n".join(lines))))
    assert len(chunks) > 1
    assert all(c.is_split_oversize and c.has_code for c in chunks)
    recovered = []
    for c in chunks:
        body = c.content.removeprefix("```shell\n").removesuffix("\n```")
        recovered.extend(body.split("\n"))
    assert recovered == lines


def test_oversize_ordered_list_splits_between_steps_keeping_numbers() -> None:
    config = ChunkingConfig(target_tokens=40, overlap_tokens=5, hard_max_tokens=80)
    steps = ListBlock(
        True, tuple(f"Do step {i} with enough words to take space." for i in range(1, 21))
    )
    chunks = Chunker(config, count).chunk(_doc(steps))
    assert len(chunks) > 1 and all(c.is_split_oversize for c in chunks)
    numbers = [line.split(".")[0] for c in chunks for line in c.content.split("\n")]
    assert numbers == [str(i) for i in range(1, 21)]


def test_oversize_table_repeats_header() -> None:
    config = ChunkingConfig(target_tokens=60, overlap_tokens=5, hard_max_tokens=100)
    table = TableBlock(
        ("Name", "Value"), tuple((f"KEY_{i}", f"value number {i}") for i in range(40))
    )
    chunks = Chunker(config, count).chunk(_doc(table))
    assert len(chunks) > 1
    assert all(c.content.startswith("| Name | Value |\n| --- | --- |") for c in chunks)


def test_overlap_uses_whole_trailing_paragraphs_only() -> None:
    config = ChunkingConfig(target_tokens=30, overlap_tokens=15, hard_max_tokens=100)
    paragraphs = [Paragraph(f"Sentence number {i} explains a detail.") for i in range(8)]
    chunks = Chunker(config, count).chunk(_doc(*paragraphs))
    assert len(chunks) > 1
    for previous, current in pairwise(chunks):
        first_para = current.content.split("\n\n")[0]
        assert first_para in previous.content  # overlap is a whole paragraph from before


def test_no_overlap_across_code() -> None:
    config = ChunkingConfig(target_tokens=30, overlap_tokens=30, hard_max_tokens=100)
    blocks = [
        CodeBlock("sh", "echo one\necho two\necho three"),
        Paragraph("After the code block text."),
        CodeBlock("sh", "echo four\necho five\necho six"),
    ]
    chunks = Chunker(config, count).chunk(_doc(*blocks))
    assert chunks[-1].content.count("```") == 2  # previous code block not duplicated as overlap


def test_unbreakable_text_is_cut() -> None:
    config = ChunkingConfig(target_tokens=50, overlap_tokens=5, hard_max_tokens=60)
    blob = "A" * 3000
    chunks = Chunker(config, count).chunk(_doc(Paragraph(blob)))
    assert "".join(c.content for c in chunks) == blob
    assert all(c.token_count <= 60 + 20 for c in chunks)
