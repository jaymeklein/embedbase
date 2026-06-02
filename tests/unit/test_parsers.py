"""Unit tests for the parser adapters.

Each test ``importorskip``s its heavy dependency so the suite degrades gracefully
where a given library isn't installed.
"""

import json

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


def test_csv_parser_row_serialization(tmp_path):
    pytest.importorskip("pandas")
    from api.adapters.parsers.csv_parser import CSVParser

    path = tmp_path / "data.csv"
    path.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")

    chunks = CSVParser().parse(str(path), DOC_ID)
    assert chunks
    text = chunks[0].text
    assert "name=Alice" in text
    assert "age=30" in text
    assert chunks[0].metadata.columns == ["name", "age"]
    assert chunks[0].metadata.total_rows == 2


def test_json_parser_array_of_objects(tmp_path):
    pytest.importorskip("tiktoken")
    from api.adapters.parsers.json_parser import JSONParser

    path = tmp_path / "arr.json"
    path.write_text(json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]), encoding="utf-8")

    chunks = JSONParser().parse(str(path), DOC_ID)
    assert chunks
    assert chunks[0].metadata.json_shape == "array"
    assert "a=1" in chunks[0].text


def test_json_parser_nested_object(tmp_path):
    pytest.importorskip("tiktoken")
    from api.adapters.parsers.json_parser import JSONParser

    path = tmp_path / "nested.json"
    path.write_text(json.dumps({"user": {"name": "X", "roles": ["a", "b"]}}), encoding="utf-8")

    chunks = JSONParser().parse(str(path), DOC_ID)
    assert chunks
    assert chunks[0].metadata.json_shape == "object"
    assert "user.name=X" in chunks[0].text


def test_code_parser_python_symbols(tmp_path):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_python")
    pytest.importorskip("tiktoken")
    from api.adapters.parsers.code import CodeParser

    src = "def greet(name):\n    return name\n\n\nclass Greeter:\n    def hi(self):\n        return 1\n"
    path = tmp_path / "mod.py"
    path.write_text(src, encoding="utf-8")

    chunks = CodeParser().parse(str(path), DOC_ID)
    names = {c.metadata.symbol_name for c in chunks}
    assert "greet" in names
    assert "Greeter" in names
    assert all(c.metadata.language == "python" for c in chunks)
