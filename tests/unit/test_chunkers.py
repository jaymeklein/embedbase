"""Unit tests for the chunking strategies."""

import pytest

pytest.importorskip("tiktoken")

from api.services.ingestion import (  # noqa: E402
    ast_boundary,
    count_tokens,
    heading_aware,
    passthrough,
    row_based,
    sliding_window,
)


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


def test_heading_aware_keeps_small_sections_whole():
    sections = ["Section one text", "Section two text"]
    out = heading_aware(sections, max_tokens=512, overlap_tokens=64)
    assert out == sections


def test_heading_aware_recurses_oversized_section():
    big = "token " * 1000
    out = heading_aware([big], max_tokens=100, overlap_tokens=20)
    assert len(out) > 1


def test_row_based_groups_rows():
    rows = [str(i) for i in range(25)]
    batches = row_based(rows, rows_per_chunk=10)
    assert [len(b) for b in batches] == [10, 10, 5]


def test_row_based_rejects_zero():
    with pytest.raises(ValueError):
        row_based(["a"], rows_per_chunk=0)


def test_ast_boundary_passthrough_filters_blank():
    assert ast_boundary(["def f(): pass", "   ", "class C: pass"]) == [
        "def f(): pass",
        "class C: pass",
    ]


def test_passthrough():
    assert passthrough("content") == ["content"]
    assert passthrough("") == []
