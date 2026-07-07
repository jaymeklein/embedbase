"""Unit test for the PDF parser's per-page progress callback (pdf.py)."""

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF

from api.adapters.parsers.pdf import PDFParser  # noqa: E402


def _make_pdf(path, pages: int) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} has some text.")
    doc.save(str(path))
    doc.close()


def test_on_progress_fires_once_per_page(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=3)

    calls: list[tuple[int, int]] = []
    chunks = PDFParser().parse(
        str(pdf), "doc_1", on_progress=lambda current, total: calls.append((current, total))
    )

    assert len(chunks) == 3  # one chunk per non-empty page
    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_parse_without_callback_still_works(tmp_path):
    """The callback is optional — omitting it must not change parsing."""
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=2)
    assert len(PDFParser().parse(str(pdf), "doc_1")) == 2


def test_char_count_matches_stored_text_with_heading(tmp_path):
    """char_count reflects the STORED chunk text (heading breadcrumb included), not the raw
    page text — so it equals len(chunk.text) even when a heading is prepended."""
    pdf = tmp_path / "heading.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "1 Introduction", fontsize=16)  # heading — larger font
    page.insert_text((72, 110), "Body sentence one.", fontsize=10)
    page.insert_text((72, 130), "Body sentence two.", fontsize=10)
    doc.save(str(pdf))
    doc.close()

    chunks = PDFParser().parse(str(pdf), "doc_1")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.metadata.heading_path == "1 Introduction"  # heading detected
    assert chunk.text.startswith("1 Introduction")  # breadcrumb prepended to the body
    assert chunk.metadata.char_count == len(chunk.text)  # counts stored text, not raw


def test_heading_merge_is_bounded(tmp_path):
    """A run of heading-size lines with no section number must not accrete into one unbounded
    heading_path — the wrapped-title merge stops at _HEADING_MAX_WORDS."""
    from api.adapters.parsers.pdf import _HEADING_MAX_WORDS

    pdf = tmp_path / "runaway.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    # Body lines (fontsize 10) dominate, so the modal body size is 10 and the 16pt lines below
    # qualify as headings.
    for i in range(24):
        page.insert_text((72, y), f"body line {i} words here", fontsize=10)
        y += 14
    page.insert_text((72, y), "7 Real Section", fontsize=16)  # a numbered heading
    y += 20
    for i in range(15):  # many heading-size, non-numbered lines
        page.insert_text((72, y), f"stray big line {i}", fontsize=16)
        y += 20
    doc.save(str(pdf))
    doc.close()

    chunks = PDFParser().parse(str(pdf), "doc_1")
    assert len(chunks) == 1
    heading_path = chunks[0].metadata.heading_path
    assert heading_path.startswith("7 Real Section")  # the numbered heading was detected
    assert len(heading_path.split()) <= _HEADING_MAX_WORDS  # bounded, not ~60 words
