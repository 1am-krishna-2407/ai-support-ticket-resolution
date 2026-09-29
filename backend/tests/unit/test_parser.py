"""Parser tests.

Golden files: real GitLab docs pages (CC BY-SA 4.0, see tests/fixtures/html/README.md)
are parsed and compared with a reviewed snapshot. Regenerate after an intentional
parser change with:  UPDATE_GOLDEN=1 pytest tests/unit/test_parser.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.services.ingestion.parser import (
    Callout,
    CodeBlock,
    GitLabDocsParser,
    Heading,
    ListBlock,
    Paragraph,
    ParseError,
    TableBlock,
    strip_tracking_params,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HTML_DIR = FIXTURES / "html"
GOLDEN_DIR = FIXTURES / "golden"
PAGES = sorted(p.stem for p in HTML_DIR.glob("*.html"))
parser = GitLabDocsParser()


def _parse_fixture(name: str):  # type: ignore[no-untyped-def]
    html = (HTML_DIR / f"{name}.html").read_text(encoding="utf-8")
    return parser.parse(html, "https://docs.gitlab.com/" + name.replace("_", "/") + "/")


@pytest.mark.parametrize("name", PAGES)
def test_golden(name: str) -> None:
    actual = _parse_fixture(name).to_dict()
    golden = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(actual)) == expected


@pytest.mark.parametrize("name", PAGES)
def test_navigation_and_chrome_are_removed(name: str) -> None:
    doc = _parse_fixture(name)
    text = json.dumps(doc.to_dict(), ensure_ascii=False)
    for noise in (
        "Was this page helpful",
        "Copy to clipboard",
        "Getting started",
        "GitLab DocsUse",
    ):
        assert noise not in text


def test_real_page_structure() -> None:
    doc = _parse_fixture("ci_variables")
    assert doc.title == "CI/CD variables"
    headings = [b for b in doc.blocks if isinstance(b, Heading)]
    assert headings[0] == Heading(2, "Predefined CI/CD variables", "predefined-cicd-variables")
    code = next(b for b in doc.blocks if isinstance(b, CodeBlock))
    assert code.language == "yaml"
    assert code.code.startswith("job1:\n  stage: test\n")
    labels = {b.label for b in doc.blocks if isinstance(b, Callout)}
    assert {"Warning", "Note", "History"} <= labels
    procedures = [b for b in doc.blocks if isinstance(b, ListBlock) and b.ordered]
    assert any("Settings > CI/CD" in item for p in procedures for item in p.items)
    assert any(
        p.text.startswith("Availability: Tier:") for p in doc.blocks if isinstance(p, Paragraph)
    )


def test_table_extraction() -> None:
    doc = _parse_fixture("user_project_integrations_webhooks")
    table = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert table.header == ("Header", "Description", "Example")
    assert table.rows[0][0] == "`Idempotency-Key`"


def test_inline_code_soft_wraps_and_br() -> None:
    html = """<main><div class="docs-content"><h1>T</h1><div>
      <p>Run
         <code>gitlab-runner register</code> now.<br>Next line.</p></div></div></main>"""
    doc = parser.parse(html, "https://docs.gitlab.com/t/")
    assert doc.blocks == (Paragraph("Run `gitlab-runner register` now.\nNext line."),)


def test_nested_list_items_keep_sub_steps_and_code() -> None:
    html = """<main><h1>T</h1><ol>
      <li>Open settings.<ul><li>Pick A.</li><li>Pick B.</li></ul></li>
      <li>Run:<div class="codeblock-wrapper"><pre><code class="language-shell">echo hi
echo bye</code></pre></div></li></ol></main>"""
    [block] = parser.parse(html, "https://docs.gitlab.com/t/").blocks
    assert isinstance(block, ListBlock) and block.ordered
    assert block.items[0] == "Open settings.\n  - Pick A.\n  - Pick B."
    assert block.items[1] == "Run:\n```shell\necho hi\necho bye\n```"


def test_links_are_absolute_and_tracking_free() -> None:
    html = """<main><div class="docs-content"><h1>T</h1><div>
      <p><a href="/ci/?utm_source=x&amp;tab=1">CI</a> <a href="#local">skip</a>
         <a href="mailto:a@b.c">mail</a></p></div></div></main>"""
    doc = parser.parse(html, "https://docs.gitlab.com/t/")
    assert [(link.text, link.url) for link in doc.links] == [
        ("CI", "https://docs.gitlab.com/ci/?tab=1")
    ]


def test_strip_tracking_params() -> None:
    assert (
        strip_tracking_params("https://x.test/a?utm_medium=m&gclid=1&q=2#f")
        == "https://x.test/a?q=2#f"
    )


@pytest.mark.parametrize(
    "html",
    ["", "<html><body></body></html>", "<main><h1>Only a title</h1></main>"],
)
def test_empty_documents_are_rejected(html: str) -> None:
    with pytest.raises(ParseError):
        parser.parse(html, "https://docs.gitlab.com/t/")


def test_content_hash_ignores_markup_noise() -> None:
    a = parser.parse('<main><h1>T</h1><p class="x">Hello</p></main>', "https://docs.gitlab.com/t/")
    b = parser.parse(
        '<main><h1>T</h1><script>var build=123</script><p class="y">Hello</p></main>',
        "https://docs.gitlab.com/t/",
    )
    c = parser.parse("<main><h1>T</h1><p>Hello!</p></main>", "https://docs.gitlab.com/t/")
    assert a.content_hash == b.content_hash != c.content_hash
