"""Unit tests for the parser adapters.

Each test ``importorskip``s its heavy dependency so the suite degrades gracefully
where a given library isn't installed.
"""

import pytest

DOC_ID = "doc_test123"


def test_pdf_parser_page_per_chunk(tmp_path):
    fitz = pytest.importorskip("fitz")
    from api.adapters.parsers.pdf import PDFParser

    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for text in ("First page text", "Second page text"):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()

    chunks = PDFParser().parse(str(path), DOC_ID)
    assert len(chunks) == 2
    assert chunks[0].metadata.parser == "pdf"
    assert chunks[0].metadata.page_number == 1
    assert chunks[0].metadata.total_pages == 2
    assert chunks[0].metadata.document_id == DOC_ID
    # Deterministic, distinct chunk IDs.
    assert chunks[0].id != chunks[1].id


def test_txt_parser_detects_and_chunks(tmp_path):
    pytest.importorskip("chardet")
    pytest.importorskip("tiktoken")
    from api.adapters.parsers.txt import TXTParser

    path = tmp_path / "sample.txt"
    path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")

    chunks = TXTParser().parse(str(path), DOC_ID)
    assert chunks
    assert chunks[0].metadata.parser == "txt"
    assert chunks[0].metadata.encoding is not None
    assert "First paragraph." in chunks[0].text


def test_markdown_parser_heading_path(tmp_path):
    pytest.importorskip("mistune")
    pytest.importorskip("tiktoken")
    from api.adapters.parsers.markdown import MarkdownParser

    md = "# Title\n\nIntro text.\n\n## Section A\n\nBody of A.\n"
    path = tmp_path / "doc.md"
    path.write_text(md, encoding="utf-8")

    chunks = MarkdownParser().parse(str(path), DOC_ID)
    assert chunks
    paths = [c.metadata.heading_path for c in chunks]
    assert "Title" in paths
    assert any(p == "Title > Section A" for p in paths)
