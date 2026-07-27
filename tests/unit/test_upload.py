"""Unit tests for the streaming upload size guard."""

import pytest
from fastapi import HTTPException

from api.services.upload import stream_upload_with_size_guard, validate_content
from tests.unit.fakes import FakeUpload


async def test_writes_file_and_returns_byte_count(tmp_path):
    dest = tmp_path / "out.txt"
    written = await stream_upload_with_size_guard(
        FakeUpload(b"hello world"), dest, max_bytes=1000
    )
    assert written == 11
    assert dest.read_bytes() == b"hello world"


async def test_creates_parent_directories(tmp_path):
    dest = tmp_path / "nested" / "deep" / "out.txt"
    await stream_upload_with_size_guard(FakeUpload(b"x"), dest, max_bytes=1000)
    assert dest.exists()


async def test_no_tmp_file_left_behind(tmp_path):
    dest = tmp_path / "out.txt"
    await stream_upload_with_size_guard(FakeUpload(b"data"), dest, max_bytes=1000)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


async def test_content_length_fast_path_rejects_before_reading(tmp_path):
    dest = tmp_path / "big.bin"
    # Declared size exceeds the limit → reject up front.
    upload = FakeUpload(b"irrelevant", size=10_000)
    with pytest.raises(HTTPException) as exc:
        await stream_upload_with_size_guard(upload, dest, max_bytes=100)
    assert exc.value.status_code == 413
    assert not dest.exists()
    assert list(tmp_path.glob("*.tmp")) == []


async def test_streaming_overflow_aborts_and_cleans_up(tmp_path):
    dest = tmp_path / "big.bin"
    # No declared size, but the body exceeds the limit while streaming.
    upload = FakeUpload(b"A" * 500, size=None)
    with pytest.raises(HTTPException) as exc:
        await stream_upload_with_size_guard(upload, dest, max_bytes=100)
    assert exc.value.status_code == 413
    assert not dest.exists()
    assert list(tmp_path.glob("*.tmp")) == []


async def test_exact_limit_is_accepted(tmp_path):
    dest = tmp_path / "ok.bin"
    written = await stream_upload_with_size_guard(
        FakeUpload(b"A" * 100), dest, max_bytes=100
    )
    assert written == 100
    assert dest.exists()


# ── Content-type validation (magic bytes at confirm time) ─────────────────────

_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


@pytest.mark.parametrize(
    "ext,head",
    [
        (".pdf", b"%PDF-1.7\n1 0 obj"),
        (".pdf", b"\x00\x00%PDF-1.4"),  # a few junk bytes before %PDF- are tolerated
        (".docx", b"PK\x03\x04\x14\x00"),
        (".pptx", b"PK\x03\x04rest"),
        (".xlsx", b"PK\x03\x04rest"),
        (".txt", b"just some plain text\n"),
        (".md", "# título com acento é\n".encode()),  # UTF-8 text is fine
        (".csv", b"a,b,c\n1,2,3\n"),
        (".txt", b""),  # empty text is allowed
        (".txt", "﻿bom text".encode()),  # UTF-8 BOM
        (".txt", "unicode".encode("utf-16")),  # UTF-16 (BOM + NUL bytes) is still valid text
        (".csv", "a,b\n1,2\n".encode("utf-16")),
        (".zip", b"PK\x03\x04"),  # ext with no known signature → not content-checked
        (".xyz", _PNG),  # unknown ext → allowed even for binary
    ],
)
def test_validate_content_accepts_matching(ext, head):
    validate_content(ext, head)  # must not raise


@pytest.mark.parametrize(
    "ext,head",
    [
        (".pdf", _PNG),  # a PNG uploaded as a .pdf — the headline case
        (".pdf", b"this is definitely not a pdf"),
        (".docx", b"plain text, not a zip container"),
        (".xlsx", b"%PDF-1.4 not a spreadsheet"),
        (".md", _PNG),  # a PNG renamed .md
        (".txt", b"looks texty\x00but has a NUL"),
        (".md", b"%PDF-1.4 a pdf pretending to be markdown"),
    ],
)
def test_validate_content_rejects_mismatch(ext, head):
    with pytest.raises(HTTPException) as exc:
        validate_content(ext, head)
    assert exc.value.status_code == 415
