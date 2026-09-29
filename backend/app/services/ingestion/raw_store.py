"""Content-addressed storage for raw fetched documents (git-ignored)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, document_id: str, body: bytes, extension: str = "html") -> Path:
        if not _SAFE_ID.match(document_id):
            raise ValueError(f"unsafe document_id for storage: {document_id!r}")
        digest = hashlib.sha256(body).hexdigest()
        directory = self.root / document_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.{extension}"
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        return path
