"""Evidence context and prompt-injection screening.

Retrieved documentation and ticket text are untrusted. They are wrapped in explicit
delimiters, any delimiter-like sequences inside them are neutralised, evidence is added
in rank order until the token budget is reached, and chunks that look like prompt
injection are excluded from the model's context.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.schemas.retrieval import RetrievedChunk

_INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|"
    r"your|system)\s+(instructions?|prompts?|rules?|messages?)"
    r"|\b(reveal|print|show|repeat|output)\s+(your|the)\s+(system\s+)?(prompt|instructions)"
    r"|\byou\s+are\s+now\s+(a|an|in)\b"
    r"|\bnew\s+instructions\s*:"
    r"|\b(developer|jailbreak|dan)\s+mode\b"
    r"|<\|?(system|im_start|im_end)\|?>"
    r"|^\s*(system|assistant)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_DELIMITER = re.compile(r"<{3,}|>{3,}")
_SAFE_ANGLE = chr(0x2039)  # single angle quotation mark, looks like "<" but is inert


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def neutralize(text: str) -> str:
    """Remove sequences that could fake our <<<...>>> delimiters."""
    return _DELIMITER.sub(lambda m: _SAFE_ANGLE * len(m.group(0)), text)


def render_ticket(title: str, description: str, environment: dict[str, str | None] | None) -> str:
    lines = [f"Title: {neutralize(title)}", "Description:", neutralize(description)]
    env = {k: v for k, v in (environment or {}).items() if v}
    if env:
        lines.append(
            "Environment: " + ", ".join(f"{k}={neutralize(str(v))}" for k, v in env.items())
        )
    return "\n".join(lines)


def render_source(chunk: RetrievedChunk) -> str:
    return "\n".join(
        [
            f"<<<SOURCE {chunk.source_id}>>>",
            f"Title: {neutralize(chunk.title)}",
            f"Section: {neutralize(chunk.heading_path)}",
            "Content:",
            neutralize(chunk.content),
            f"<<<END SOURCE {chunk.source_id}>>>",
        ]
    )


@dataclass(frozen=True)
class EvidenceContext:
    text: str
    used: tuple[RetrievedChunk, ...]
    dropped_for_budget: tuple[str, ...]
    excluded_for_injection: tuple[str, ...]


def build_evidence_context(
    evidence: Sequence[RetrievedChunk],
    *,
    max_tokens: int,
    count_tokens: Callable[[str], int],
) -> EvidenceContext:
    """Add sources in rank order until `max_tokens`; lowest-ranked sources are dropped first."""
    used: list[RetrievedChunk] = []
    rendered: list[str] = []
    dropped: list[str] = []
    excluded: list[str] = []
    total = 0
    for chunk in evidence:
        if looks_like_injection(chunk.content):
            excluded.append(chunk.source_id)
            continue
        block = render_source(chunk)
        tokens = count_tokens(block)
        if total + tokens > max_tokens:
            dropped.append(chunk.source_id)
            continue
        used.append(chunk)
        rendered.append(block)
        total += tokens
    return EvidenceContext("\n\n".join(rendered), tuple(used), tuple(dropped), tuple(excluded))
