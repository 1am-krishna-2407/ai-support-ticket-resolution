"""Versioned prompt templates loaded from app/prompts/<name>_v<N>.txt.

Templates use `string.Template` ($placeholders) so literal braces in JSON or code
inside tickets and documentation never break formatting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
_NAME = re.compile(r"^(?P<name>[a-z_]+)_v(?P<version>\d+)\.txt$")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    template: Template

    def render(self, **values: str) -> str:
        return self.template.substitute(**values)


class PromptRegistry:
    """Loads prompt files; `get(name)` returns the latest version unless pinned."""

    def __init__(self, directory: Path = PROMPT_DIR, pins: dict[str, str] | None = None) -> None:
        self._prompts: dict[str, dict[str, PromptTemplate]] = {}
        for path in sorted(directory.glob("*.txt")):
            match = _NAME.match(path.name)
            if not match:
                continue
            name, version = match["name"], f"v{match['version']}"
            self._prompts.setdefault(name, {})[version] = PromptTemplate(
                name, version, Template(path.read_text(encoding="utf-8"))
            )
        self._pins = pins or {}

    def get(self, name: str) -> PromptTemplate:
        versions = self._prompts.get(name)
        if not versions:
            raise KeyError(f"no prompt named {name!r}")
        pinned = self._pins.get(name)
        if pinned:
            return versions[pinned]
        latest = max(versions, key=lambda v: int(v[1:]))
        return versions[latest]

    def versions(self) -> dict[str, str]:
        return {name: self.get(name).version for name in sorted(self._prompts)}
