"""Chunking strategies shared by the parser adapters.

Each strategy turns a blob of text (or rows) into a list of string segments.
Parsers attach metadata and build :class:`~api.models.chunk.Chunk` objects from
these segments. Token counting uses ``tiktoken``'s ``cl100k_base`` encoding so
chunk sizes line up with the OpenAI-family tokenizers most embedders track.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tiktoken


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def sliding_window(
    text: str,
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[str]:
    """Split ``text`` into overlapping windows of at most ``max_tokens`` tokens.

    Consecutive windows share ``overlap_tokens`` tokens so context that straddles
    a boundary is not lost. Returns ``[]`` for empty/whitespace-only input.
    """
    if not text or not text.strip():
        return []
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")

    enc = _encoder()
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    step = max_tokens - overlap_tokens
    windows: list[str] = []
    for start in range(0, len(tokens), step):
        window = tokens[start : start + max_tokens]
        if not window:
            break
        windows.append(enc.decode(window).strip())
        if start + max_tokens >= len(tokens):
            break
    return [w for w in windows if w]


def heading_aware(
    sections: list[str],
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[str]:
    """Keep each section whole; recurse oversized sections into sliding windows."""
    out: list[str] = []
    for section in sections:
        if not section.strip():
            continue
        if count_tokens(section) <= max_tokens:
            out.append(section)
        else:
            out.extend(
                sliding_window(
                    section, max_tokens=max_tokens, overlap_tokens=overlap_tokens
                )
            )
    return out


def ast_boundary(symbols: list[str]) -> list[str]:
    """Pass-through — a code symbol is treated as an atomic chunk."""
    return [s for s in symbols if s.strip()]


def row_based(rows: list[str], *, rows_per_chunk: int = 10) -> list[list[str]]:
    """Group serialized rows into batches of ``rows_per_chunk``."""
    if rows_per_chunk < 1:
        raise ValueError("rows_per_chunk must be >= 1")
    return [rows[i : i + rows_per_chunk] for i in range(0, len(rows), rows_per_chunk)]


def passthrough(text: str) -> list[str]:
    """Identity — a single non-empty chunk, or nothing."""
    return [text] if text and text.strip() else []
