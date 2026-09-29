"""Token counting for chunk sizing and prompt budgets (ADR D13)."""

from __future__ import annotations

from functools import lru_cache

import tiktoken


class Tokenizer:
    def __init__(self, encoding_name: str) -> None:
        self.encoding_name = encoding_name
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))


@lru_cache(maxsize=4)
def get_tokenizer(encoding_name: str) -> Tokenizer:
    return Tokenizer(encoding_name)
