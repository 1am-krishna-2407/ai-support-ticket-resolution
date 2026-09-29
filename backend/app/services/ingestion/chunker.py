"""Structure-aware chunking.

Rules:
- Chunks never cross a heading boundary; each carries its heading path.
- Blocks are packed up to `target_tokens`. Code blocks, lists (numbered
  procedures), tables, and callouts are atomic: they are split only when a
  single block exceeds `hard_max_tokens`, at line/item/row boundaries, and the
  resulting chunks are flagged `is_split_oversize`.
- Overlap between consecutive chunks of the same section is made of whole
  trailing paragraphs from the previous chunk, at most `overlap_tokens`.
- `embedding_text` = document title + heading path + content, so the embedding
  carries context; `content` stays readable.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.services.ingestion.parser import (
    Block,
    Callout,
    CodeBlock,
    Heading,
    ListBlock,
    Paragraph,
    ParsedDocument,
    TableBlock,
    render_block,
    render_table_markdown,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])")


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int
    overlap_tokens: int
    hard_max_tokens: int


@dataclass(frozen=True)
class ChunkDraft:
    index: int
    heading_path: tuple[str, ...]  # title first
    section: str | None
    subsection: str | None
    anchor: str | None
    content: str
    embedding_text: str
    token_count: int
    has_code: bool
    is_split_oversize: bool

    @property
    def content_hash(self) -> str:
        """Hash of the exact embedded text: identical hash ⇒ identical embedding input."""
        return hashlib.sha256(self.embedding_text.encode("utf-8")).hexdigest()


def make_chunk_id(document_id: str, version: int, index: int, content_hash: str) -> str:
    key = f"{document_id}|{version}|{index}|{content_hash}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class _Unit:
    text: str
    tokens: int
    is_paragraph: bool
    has_code: bool
    split: bool = False
    is_heading: bool = False


@dataclass
class _Section:
    path: tuple[str, ...]
    anchor: str | None
    heading: Heading | None
    blocks: list[Block]


class Chunker:
    def __init__(self, config: ChunkingConfig, count_tokens: Callable[[str], int]) -> None:
        self.config = config
        self._count = count_tokens

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for section in self._sections(document):
            units = self._units(section)
            if not units:
                continue
            for group, split in self._pack(units):
                content = "\n\n".join(u.text for u in group)
                embedding_text = "\n".join((" > ".join(section.path), "", content))
                drafts.append(
                    ChunkDraft(
                        index=len(drafts),
                        heading_path=section.path,
                        section=section.path[1] if len(section.path) > 1 else None,
                        subsection=section.path[2] if len(section.path) > 2 else None,
                        anchor=section.anchor,
                        content=content,
                        embedding_text=embedding_text,
                        token_count=self._count(embedding_text),
                        has_code=any(u.has_code for u in group),
                        is_split_oversize=split,
                    )
                )
        return drafts

    # --- sections ---------------------------------------------------------------

    @staticmethod
    def _sections(document: ParsedDocument) -> list[_Section]:
        sections = [_Section((document.title,), None, None, [])]
        stack: list[Heading] = []
        for block in document.blocks:
            if isinstance(block, Heading):
                stack = [h for h in stack if h.level < block.level] + [block]
                path = (document.title, *(h.text for h in stack))
                sections.append(_Section(path, block.anchor, block, []))
            else:
                sections[-1].blocks.append(block)
        return [s for s in sections if s.blocks]

    def _units(self, section: _Section) -> list[_Unit]:
        units: list[_Unit] = []
        if section.heading is not None:
            text = render_block(section.heading)
            units.append(
                _Unit(text, self._count(text), is_paragraph=False, has_code=False, is_heading=True)
            )
        for block in section.blocks:
            text = render_block(block)
            tokens = self._count(text)
            has_code = isinstance(block, CodeBlock) or "```" in text
            if tokens <= self.config.hard_max_tokens:
                units.append(_Unit(text, tokens, isinstance(block, Paragraph), has_code))
            else:
                units.extend(
                    _Unit(piece, self._count(piece), False, has_code, split=True)
                    for piece in self._split_oversize(block)
                )
        return units

    # --- packing ----------------------------------------------------------------

    def _pack(self, units: list[_Unit]) -> list[tuple[list[_Unit], bool]]:
        groups: list[tuple[list[_Unit], bool]] = []
        current: list[_Unit] = []
        current_tokens = 0
        fresh = 0  # content units in `current` not carried over as overlap

        def flush() -> None:
            if fresh:
                groups.append((list(current), any(u.split for u in current)))

        for unit in units:
            # A heading is never emitted alone: it always travels with the next content unit.
            if current and current_tokens + unit.tokens > self.config.target_tokens and fresh:
                flush()
                overlap = self._overlap(current)
                # Keep overlap only if it still leaves room for the next unit.
                if sum(u.tokens for u in overlap) + unit.tokens > self.config.hard_max_tokens:
                    overlap = []
                current = overlap
                current_tokens = sum(u.tokens for u in current)
                fresh = 0
            current.append(unit)
            current_tokens += unit.tokens
            if not unit.is_heading:
                fresh += 1
        flush()
        return groups

    def _overlap(self, units: list[_Unit]) -> list[_Unit]:
        carried: list[_Unit] = []
        total = 0
        for unit in reversed(units):
            if not unit.is_paragraph or total + unit.tokens > self.config.overlap_tokens:
                break
            carried.insert(0, unit)
            total += unit.tokens
        return carried

    # --- oversize splitting -------------------------------------------------------

    def _split_oversize(self, block: Block) -> list[str]:
        limit = self.config.target_tokens
        match block:
            case CodeBlock(language=language, code=code):
                fence = f"```{language or ''}"
                return [
                    f"{fence}\n{part}\n```"
                    for part in self._group(code.split("\n"), "\n", limit - self._count(fence) - 4)
                ]
            case ListBlock(ordered=ordered, items=items):
                numbered = [
                    (f"{i}. " if ordered else "- ") + item.replace("\n", "\n   ")
                    for i, item in enumerate(items, 1)
                ]
                return self._group(numbered, "\n", limit)
            case TableBlock(header=header, rows=rows):
                header_md = render_table_markdown(header, ())
                row_lines = [render_table_markdown((), (r,)) for r in rows]
                budget = limit - self._count(header_md)
                return [
                    f"{header_md}\n{part}" if header else part
                    for part in self._group(row_lines, "\n", budget)
                ]
            case Paragraph(text=text) | Callout(text=text):
                prefix = f"**{block.label}:** " if isinstance(block, Callout) else ""
                return [prefix + p for p in self._group(_SENTENCE.split(text), " ", limit)]
        return [render_block(block)]  # pragma: no cover

    def _group(self, pieces: list[str], sep: str, limit: int) -> list[str]:
        """Greedily join pieces up to `limit` tokens; a single huge piece is hard-cut."""
        out: list[str] = []
        buf: list[str] = []
        buf_tokens = 0
        for piece in pieces:
            tokens = self._count(piece)
            if tokens > limit:
                if buf:
                    out.append(sep.join(buf))
                    buf, buf_tokens = [], 0
                out.extend(self._hard_cut(piece, limit))
                continue
            if buf and buf_tokens + tokens > limit:
                out.append(sep.join(buf))
                buf, buf_tokens = [], 0
            buf.append(piece)
            buf_tokens += tokens
        if buf:
            out.append(sep.join(buf))
        return out

    def _hard_cut(self, text: str, limit: int) -> list[str]:
        words = text.split(" ")
        if len(words) > 1:
            return self._group(words, " ", limit)
        # One unbreakable token run (e.g. a long base64 line): cut by characters.
        tokens = max(self._count(text), 1)
        step = max(1, len(text) * limit // tokens)
        return [text[i : i + step] for i in range(0, len(text), step)]
