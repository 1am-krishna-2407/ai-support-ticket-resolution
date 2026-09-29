"""HTML → structured document tree.

The tree keeps what matters for support answers (heading hierarchy with anchors,
paragraphs, ordered/unordered lists, code blocks with language, tables, callouts,
availability notes, feature history) and drops navigation, toolbars, feedback
widgets, scripts, and styling.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

PARSER_VERSION = "gitlab-docs-1"

_WS = re.compile(r"[ \t\r\f\v\u00a0]+")
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_|_ga$|ref$)", re.IGNORECASE)
_REMOVE_SELECTORS = (
    "script, style, noscript, template, iframe, form, button, svg, img, nav, hr, "
    ".docs-codeblock-toolbar, .feedback, .alert-icon, .open-sidebar"
)
_ROOT_SELECTORS = ("main .docs-content", "main", "article", "body")


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str | None
    kind: str = "heading"


@dataclass(frozen=True)
class Paragraph:
    text: str
    kind: str = "paragraph"


@dataclass(frozen=True)
class ListBlock:
    ordered: bool
    items: tuple[str, ...]
    kind: str = "list"


@dataclass(frozen=True)
class CodeBlock:
    language: str | None
    code: str
    kind: str = "code"


@dataclass(frozen=True)
class TableBlock:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    kind: str = "table"


@dataclass(frozen=True)
class Callout:
    label: str  # e.g. "Note", "Warning", "Flag", "History"
    text: str
    kind: str = "callout"


Block = Heading | Paragraph | ListBlock | CodeBlock | TableBlock | Callout


@dataclass(frozen=True)
class Link:
    text: str
    url: str


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    description: str | None
    blocks: tuple[Block, ...]
    links: tuple[Link, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def content_hash(self) -> str:
        """Hash of the extracted content (not raw HTML, which changes with every build)."""
        payload = json.dumps(
            {"title": self.title, "blocks": [asdict(b) for b in self.blocks]},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DocumentParser(Protocol):
    version: str

    def parse(self, html: str, base_url: str) -> ParsedDocument: ...


def _clean(text: str) -> str:
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _class_list(node: Node) -> list[str]:
    return (node.attributes.get("class") or "").split()


def strip_tracking_params(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def render_table_markdown(header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    def row(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |"

    lines = [row(header), "| " + " | ".join("---" for _ in header) + " |"] if header else []
    lines.extend(row(r) for r in rows)
    return "\n".join(lines)


def render_block(block: Block) -> str:
    """Readable markdown for a block; used for chunk content and embeddings."""
    match block:
        case Heading(level=level, text=text):
            return f"{'#' * level} {text}"
        case Paragraph(text=text):
            return text
        case ListBlock(ordered=ordered, items=items):
            return "\n".join(
                (f"{i}. " if ordered else "- ") + item.replace("\n", "\n   ")
                for i, item in enumerate(items, 1)
            )
        case CodeBlock(language=language, code=code):
            return f"```{language or ''}\n{code}\n```"
        case TableBlock(header=header, rows=rows):
            return render_table_markdown(header, rows)
        case Callout(label=label, text=text):
            return f"**{label}:** {text}"
    raise TypeError(f"unknown block {block!r}")  # pragma: no cover


class GitLabDocsParser:
    """Parser tuned for docs.gitlab.com; falls back to generic regions for other HTML."""

    version = PARSER_VERSION

    def parse(self, html: str, base_url: str) -> ParsedDocument:
        tree = HTMLParser(html)
        root = next((n for sel in _ROOT_SELECTORS if (n := tree.css_first(sel)) is not None), None)
        if root is None:
            raise ParseError("document has no content region")

        title = self._title(tree, root)
        description_node = tree.css_first('meta[name="description"]')
        description = (
            _clean(description_node.attributes.get("content") or "") or None
            if description_node
            else None
        )
        links = self._links(root, base_url)

        # Collapsible labels live in <button>s, which are removed below; keep them first.
        for section in root.css(".collapsible-section"):
            label_node = section.css_first("[data-toggle], summary, .collapsible-title")
            if label_node is not None:
                section.attrs["data-label"] = _clean(label_node.text())
        for node in root.css(_REMOVE_SELECTORS):
            node.decompose()

        blocks: list[Block] = []
        self._walk(root, blocks)
        blocks = [b for b in blocks if not (isinstance(b, Heading) and b.level == 1)]
        if not any(not isinstance(b, Heading) for b in blocks):
            raise ParseError("no content blocks extracted")
        return ParsedDocument(
            title=title, description=description, blocks=tuple(blocks), links=links
        )

    # --- extraction helpers ---------------------------------------------------

    @staticmethod
    def _title(tree: HTMLParser, root: Node) -> str:
        h1 = root.css_first("h1") or tree.css_first("h1")
        if h1 is not None and h1.text(strip=True):
            return _clean(h1.text())
        title = tree.css_first("title")
        if title is not None:
            return _clean(title.text()).split(" | ")[0]
        raise ParseError("document has no title")

    @staticmethod
    def _links(root: Node, base_url: str) -> tuple[Link, ...]:
        seen: set[str] = set()
        links: list[Link] = []
        content = root.css_first(".docs-content > div:not([class])") or root
        for a in content.css("a[href]"):
            href = (a.attributes.get("href") or "").strip()
            text = _clean(a.text())
            if not href or href.startswith(("#", "mailto:", "javascript:")) or not text:
                continue
            url = strip_tracking_params(urljoin(base_url, href))
            if urlsplit(url).scheme not in ("http", "https") or url in seen:
                continue
            seen.add(url)
            links.append(Link(text=text, url=url))
        return tuple(links)

    def _inline(self, node: Node) -> str:
        """Text of an element with inline code in backticks, excluding nested blocks."""
        parts: list[str] = []
        for child in node.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                # Newlines in HTML source are soft wraps; only <br> is a real line break.
                parts.append(child.text(deep=False).replace("\n", " "))
            elif tag == "code":
                code = child.text()
                parts.append(f"`{code}`" if code.strip() else code)
            elif tag == "br":
                parts.append("\n")
            elif tag in ("ul", "ol", "pre", "table", "div") and node.tag == "li":
                continue  # nested blocks inside list items are rendered separately
            else:
                parts.append(self._inline(child))
        return "".join(parts)

    def _list_items(self, node: Node, depth: int = 0) -> list[str]:
        items: list[str] = []
        for li in node.iter():
            if li.tag != "li":
                continue
            lines = [_clean(self._inline(li)).replace("\n", " ")]
            for child in li.iter():
                if child.tag in ("ul", "ol"):
                    marker = "1." if child.tag == "ol" else "-"
                    lines.extend(
                        f"{'  ' * (depth + 1)}{marker} {sub}"
                        for sub in self._list_items(child, depth + 1)
                    )
                elif child.tag == "pre" or "codeblock-wrapper" in _class_list(child):
                    code = self._code(child)
                    fence = f"```{code.language or ''}"
                    lines.append("\n".join([fence, code.code, "```"]))
                elif child.tag == "table":
                    table = self._table(child)
                    if table:
                        lines.append(render_table_markdown(table.header, table.rows))
                elif child.tag == "div" and not _class_list(child):
                    # Wrapper divs inside list items (e.g. paragraphs) keep their text.
                    text = _clean(self._inline(child))
                    if text:
                        lines.append(text)
            text = "\n".join(line for line in lines if line)
            if text:
                items.append(text)
        return items

    @staticmethod
    def _code(node: Node) -> CodeBlock:
        code_node = node.css_first("code") or node.css_first("pre") or node
        language = code_node.attributes.get("data-lang")
        if not language:
            for cls in _class_list(code_node):
                if cls.startswith("language-"):
                    language = cls.removeprefix("language-")
                    break
        text = code_node.text(deep=True).rstrip("\n")
        return CodeBlock(language=(language or None), code=text)

    def _table(self, node: Node) -> TableBlock | None:
        header: tuple[str, ...] = ()
        rows: list[tuple[str, ...]] = []
        for tr in node.css("tr"):
            cells = [
                _clean(self._inline(c)).replace("\n", " ")
                for c in tr.iter()
                if c.tag in ("th", "td")
            ]
            if not cells:
                continue
            if not header and all(c.tag == "th" for c in tr.iter() if c.tag in ("th", "td")):
                header = tuple(cells)
            else:
                rows.append(tuple(cells))
        if not header and not rows:
            return None
        return TableBlock(header=header, rows=tuple(rows))

    def _callout_text(self, node: Node) -> str:
        pieces: list[str] = []
        for child in node.iter():
            if child.tag in ("ul", "ol"):
                pieces.extend(f"- {item}" for item in self._list_items(child))
            else:
                text = _clean(self._inline(child))
                if text:
                    pieces.append(text)
        return "\n".join(pieces)

    def _walk(self, node: Node, out: list[Block]) -> None:
        for child in node.iter():
            tag = child.tag
            classes = _class_list(child)
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = _clean(child.text())
                if text:
                    out.append(Heading(int(tag[1]), text, child.attributes.get("id") or None))
            elif tag == "p":
                text = _clean(self._inline(child))
                if text:
                    out.append(Paragraph(text))
            elif tag in ("ul", "ol"):
                items = self._list_items(child)
                if items:
                    out.append(ListBlock(ordered=tag == "ol", items=tuple(items)))
            elif tag == "pre" or "codeblock-wrapper" in classes:
                code = self._code(child)
                if code.code.strip():
                    out.append(code)
            elif tag == "table":
                table = self._table(child)
                if table:
                    out.append(table)
            elif "alert" in classes:
                label = next(
                    (c.removeprefix("alert-type-") for c in classes if c.startswith("alert-type-")),
                    "note",
                )
                text = self._callout_text(child)
                if text:
                    out.append(Callout(label=label.replace("-", " ").capitalize(), text=text))
            elif "availability" in classes:
                facts = [_clean(li.text()) for li in child.css("li")]
                text = "; ".join(f for f in facts if f) or _clean(child.text())
                if text:
                    out.append(Paragraph(f"Availability: {text}"))
            elif "collapsible-section" in classes:
                label = child.attributes.get("data-label") or "Details"
                body = child.css_first(".collapsible-list, .collapse") or child
                text = self._callout_text(body)
                if text and label.lower() == "history":
                    out.append(Callout(label="History", text=text))
                elif text:
                    self._walk(body, out)
            elif tag == "blockquote":
                text = self._callout_text(child)
                if text:
                    out.append(Callout(label="Quote", text=text))
            elif tag == "dl":
                for item in child.iter():
                    text = _clean(self._inline(item))
                    if text:
                        out.append(Paragraph(text if item.tag == "dd" else f"**{text}**"))
            else:
                self._walk(child, out)
