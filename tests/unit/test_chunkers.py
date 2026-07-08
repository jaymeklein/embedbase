"""Unit tests for the chunking strategies."""

import pytest

pytest.importorskip("tiktoken")

from api.services.ingestion import count_tokens, sliding_window  # noqa: E402


def test_sliding_window_short_text_single_chunk():
    assert sliding_window("hello world", max_tokens=512, overlap_tokens=64) == [
        "hello world"
    ]


def test_sliding_window_empty_returns_empty():
    assert sliding_window("", max_tokens=512, overlap_tokens=64) == []
    assert sliding_window("   \n  ", max_tokens=512, overlap_tokens=64) == []


def test_sliding_window_long_text_splits():
    text = "word " * 2000
    windows = sliding_window(text, max_tokens=100, overlap_tokens=20)
    assert len(windows) > 1
    for w in windows:
        assert count_tokens(w) <= 100


def test_sliding_window_overlap_must_be_smaller_than_max():
    with pytest.raises(ValueError):
        sliding_window("anything at all here", max_tokens=64, overlap_tokens=64)
