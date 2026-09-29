"""Query transformation: normalise text and extract technical identifiers verbatim.

Support tickets mix prose with exact strings (error messages, CLI flags, CI/CD
variables, config keys, versions, HTTP codes, file names). Those are extracted
with deterministic patterns so lexical search can match them exactly and so
they are never paraphrased away.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# straight, backtick, and curly quotes (curly ones built with chr() to keep the source ASCII)
_QUOTE_CHARS = "\"'`" + "".join(map(chr, (0x201C, 0x201D, 0x2018, 0x2019)))
_QUOTED = re.compile(f"[{_QUOTE_CHARS}]([^{_QUOTE_CHARS}\n]{{4,200}})[{_QUOTE_CHARS}]")
_ERROR_LINE = re.compile(
    r"(?im)^.*\b(error|fatal|failed|failure|denied|refused|unauthorized|forbidden|"
    r"exception|timeout|timed out|x509|certificate|not found|unable to|cannot|could not)\b.*$"
)
_PATTERNS: dict[str, re.Pattern[str]] = {
    "ci_variable": re.compile(r"\$?\b(CI_[A-Z0-9_]+|GITLAB_[A-Z0-9_]+|DOCKER_[A-Z0-9_]+)\b"),
    "cli_flag": re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{1,40})(?:=\S+)?"),
    "http_status": re.compile(r"\b((?:HTTP[/ ]?\d(?:\.\d)?\s+)?[45]\d\d)\b(?=[\s:,.)]|$)"),
    "version": re.compile(r"\b(v?\d{1,2}\.\d{1,3}(?:\.\d{1,3})?(?:-[a-z0-9.]+)?)\b"),
    "file_name": re.compile(
        r"(?<![\w/])(\.?[\w.-]+\.(?:ya?ml|toml|json|rb|conf|cfg|ini|pem|crt|key|sh|env|lock))\b"
    ),
    "config_key": re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+){1,6})\b"),
    "command": re.compile(
        r"\b((?:gitlab-runner|gitlab-ctl|gitlab-rake|glab|git|docker|kubectl|helm|curl)"
        r"(?:\s+[a-z][\w-]*){1,3})"
    ),
}
_ERROR_STRINGS_MAX = 5
_TERMS_MAX = 40


@dataclass(frozen=True)
class TransformedQuery:
    original: str
    normalized: str
    retrieval_query: str
    terms: dict[str, list[str]] = field(default_factory=dict)
    phrases: tuple[str, ...] = ()  # exact strings for phrase matching

    @property
    def flat_terms(self) -> list[str]:
        return [t for values in self.terms.values() for t in values]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def extract_terms(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    seen: set[str] = set()
    total = 0
    for kind, pattern in _PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            key = value.lower()
            if len(value) < 2 or key in seen:
                continue
            seen.add(key)
            found.setdefault(kind, []).append(value)
            total += 1
            if total >= _TERMS_MAX:
                return found
    return found


def extract_phrases(text: str) -> list[str]:
    """Quoted strings and error-looking lines, kept verbatim for exact matching."""
    phrases: list[str] = []
    for match in _QUOTED.finditer(text):
        phrases.append(match.group(1).strip())
    for match in _ERROR_LINE.finditer(text):
        line = match.group(0).strip().strip("`'\"")
        if 8 <= len(line) <= 200:
            phrases.append(line)
    unique: list[str] = []
    for phrase in phrases:
        if phrase.lower() not in {p.lower() for p in unique}:
            unique.append(phrase)
    return unique[:_ERROR_STRINGS_MAX]


def transform_query(title: str, description: str = "", *, max_chars: int) -> TransformedQuery:
    """Build the retrieval query from a ticket (or a plain search string as `title`)."""
    title_n = normalize_text(title)
    description_n = normalize_text(description)
    combined = "\n".join(part for part in (title_n, description_n) if part)
    terms = extract_terms(combined)
    phrases = extract_phrases(combined)

    # Title first, then the most informative description lines (errors first), then terms.
    lines = description_n.split("\n") if description_n else []
    error_lines = [ln for ln in lines if _ERROR_LINE.match(ln)]
    other_lines = [ln for ln in lines if ln not in error_lines]
    parts = [title_n, *error_lines, *other_lines]
    flat = [t for values in terms.values() for t in values]
    body = "\n".join(p for p in parts if p)
    suffix = " ".join(t for t in flat if t.lower() not in body.lower())
    retrieval_query = f"{body}\n{suffix}".strip() if suffix else body
    if len(retrieval_query) > max_chars:
        # Keep the extracted identifiers: trim prose, not terms.
        keep = max_chars - len(suffix) - 1 if suffix else max_chars
        retrieval_query = (body[: max(keep, 0)] + ("\n" + suffix if suffix else ""))[:max_chars]
    return TransformedQuery(
        original=combined,
        normalized=combined,
        retrieval_query=retrieval_query,
        terms=terms,
        phrases=tuple(phrases),
    )
