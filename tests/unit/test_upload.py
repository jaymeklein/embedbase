"""Unit tests for the streaming upload size guard."""

import io

import pytest
from fastapi import HTTPException

from api.services.upload import stream_upload_with_size_guard


class FakeUpload:
    """Minimal stand-in for starlette's UploadFile (``.size`` + async ``.read``)."""

    def __init__(self, data: bytes, size: int | None = None):
        self._buf = io.BytesIO(data)
        self.size = size

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


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
