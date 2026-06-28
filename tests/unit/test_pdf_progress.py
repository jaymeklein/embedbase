"""Unit test for the PDF parser's per-page progress callback (pdf.py)."""

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF

from api.adapters.parsers.pdf import PDFParser


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
