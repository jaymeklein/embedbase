"""Sliding-window chunking + tiktoken token counting, shared by the txt/markdown adapters.

``sliding_window`` turns a blob of text into a list of string segments; parsers attach
metadata and build :class:`~api.models.chunk.Chunk` objects from these segments. Token
counting uses ``tiktoken``'s ``cl100k_base`` encoding so chunk sizes line up with the
OpenAI-family tokenizers most embedders track.
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

    n = len(tokens)
    windows: list[str] = []
    start = 0
    while start < n:
        end = min(start + max_tokens, n)
        chunk = enc.decode(tokens[start:end]).strip()
        # strip() can re-tokenise the boundary token into one or two extra tokens,
        # pushing the chunk past max_tokens. Shrink the window (in original-token
        # space) until it fits — no content is dropped, since the trimmed tail is
        # re-covered by the next window's overlap.
        while count_tokens(chunk) > max_tokens and end - start > 1:
            end -= 1
            chunk = enc.decode(tokens[start:end]).strip()
        if chunk:
            windows.append(chunk)
        if end >= n:
            break
        # Advance by the *actual* window end (not a fixed step): a shrunk window
        # starts the next one earlier so the dropped tail is never skipped.
        start = max(start + 1, end - overlap_tokens)
    return windows
