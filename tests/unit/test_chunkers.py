"""Unit tests for the chunking strategies."""

import pytest

pytest.importorskip("tiktoken")

from api.adapters.parsers.txt import TXTParser  # noqa: E402
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


def test_sliding_window_natural_prose_never_exceeds_max():
    # decode()+strip() at a window boundary can nudge the re-encoded chunk one
    # token past the slice — regular "word word …" text doesn't trigger it, prose does.
    text = ("The quick brown fox jumps over the lazy dog. " * 400).strip()
    windows = sliding_window(text, max_tokens=512, overlap_tokens=64)
    assert len(windows) > 1
    for w in windows:
        assert count_tokens(w) <= 512


def test_sliding_window_overlap_zero_loses_no_content():
    # Regression: honouring the token cap must not drop content. With overlap=0
    # the windows are disjoint, so any token trimmed to fit the cap would vanish
    # outright — they must still partition the text (every character survives).
    text = " ".join(f"word{i}" for i in range(400))
    windows = sliding_window(text, max_tokens=32, overlap_tokens=0)
    assert windows
    for w in windows:
        assert count_tokens(w) <= 32
    stripped = "".join("".join(w.split()) for w in windows)
    assert stripped == "".join(text.split())


# ── TXTParser._segment: no chunk exceeds the token budget ─────────────────────
#
# The failure this guards: paragraph packing summed only *content* tokens, so the
# "\n\n" join separators (~1 token each) accumulated outside the budget — a run of
# small paragraphs produced a chunk well past max_tokens (measured 767 at limit 512).


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("\n\n".join(f"para {i}" for i in range(400)), id="many-tiny-paras"),
        pytest.param("\n\n".join("word " * 100 for _ in range(12)), id="medium-paras"),
        pytest.param("word " * 5000, id="one-huge-para"),
        pytest.param(
            ("The quick brown fox jumps over the lazy dog. " * 400).strip(), id="natural-prose"
        ),
        pytest.param(
            ("word " * 1500) + "\n\n" + "\n\n".join(f"p{i}" for i in range(300)), id="big-then-tiny"
        ),
    ],
)
def test_txt_segment_never_exceeds_max_tokens(text):
    parser = TXTParser()  # defaults: max_tokens=512, overlap=64
    chunks = parser._segment(text)
    assert chunks  # non-empty input yields at least one chunk
    for chunk in chunks:
        assert count_tokens(chunk) <= parser._max_tokens


def test_txt_parse_end_to_end_respects_limit(tmp_path):
    # Real entry point (file → chunks): many tiny paragraphs, the worst case for packing.
    doc = tmp_path / "doc.txt"
    doc.write_text("\n\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    chunks = TXTParser().parse(str(doc), "doc")
    assert chunks
    for chunk in chunks:
        assert count_tokens(chunk.text) <= 512
