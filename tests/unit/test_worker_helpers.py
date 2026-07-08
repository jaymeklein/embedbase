"""Unit tests for small, pure worker helpers with no infra needs.

``_chunk_label`` / ``_parse_with_progress`` are pure; ``_claim_job`` /
``_mark_failed`` touch a throwaway SQLite DB (``SessionLocal`` patched). The
ingestion pipeline tests exercise these only incidentally — here they're pinned
directly so a regression in a single branch fails loudly.
"""

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import worker.tasks as wt
from api.models.chunk import Chunk, ChunkMetadata
from api.tables import job_records, metadata


def _meta(**over) -> ChunkMetadata:
    base = dict(source_file="f", filename="f", parser="p", document_id="d", chunk_index=0)
    base.update(over)
    return ChunkMetadata(**base)


def _chunk(text="the quick brown fox", **meta_over) -> Chunk:
    return Chunk(id="c", text=text, metadata=_meta(**meta_over))


class _DummyRedis:
    def exists(self, *a):  # unknown-job path returns before this is reached
        return False


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wh.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


# ── _chunk_label: first non-empty of heading → page → text ───────────


def test_chunk_label_prefers_heading_path():
    assert wt._chunk_label(_chunk(heading_path="Intro > Setup")) == "Intro > Setup"


def test_chunk_label_falls_back_to_page_number():
    assert wt._chunk_label(_chunk(page_number=7)) == "p.7"


def test_chunk_label_falls_back_to_collapsed_text():
    assert wt._chunk_label(_chunk(text="  spaced   out  ")) == "spaced out"


# ── _parse_with_progress: page callbacks only when the parser opts in ─────────


def test_parse_with_progress_threads_callback_when_supported():
    seen: list[tuple] = []

    class _Parser:
        def parse(self, path, doc_id, on_progress):
            on_progress(1, 2)
            return ["chunk"]

    out = wt._parse_with_progress(_Parser(), "f.pdf", "d", lambda *a: seen.append(a))
    assert out == ["chunk"]
    assert seen == [("parsing", 1, 2)]  # page callback forwarded as a progress emit


def test_parse_with_progress_skips_callback_when_unsupported():
    def _no_emit(*a):
        raise AssertionError("a parser without on_progress must not emit page events")

    class _Parser:
        def parse(self, path, doc_id):
            return ["c"]

    assert wt._parse_with_progress(_Parser(), "f.txt", "d", _no_emit) == ["c"]


# ── _claim_job: an unknown job id is not claimable ────────────────────────────


def test_claim_job_unknown_id_returns_false(tmp_path):
    factory = _factory(tmp_path)  # schema present, no job rows
    assert wt._claim_job(factory, _DummyRedis(), "ghost") is False


# ── _mark_failed: records the failure on the job row ──────────────────────────


def test_mark_failed_records_failure(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    with factory() as s:
        s.execute(
            insert(job_records).values(
                job_id="j1", document_id="d1", collection_id="c1", filename="f",
                file_type=".txt", status="processing", created_at="t", updated_at="t",
            )
        )
        s.commit()
    monkeypatch.setattr(wt, "SessionLocal", factory)

    wt._mark_failed("j1", "kaboom")

    with factory() as s:
        row = s.execute(
            select(job_records.c.status, job_records.c.error).where(job_records.c.job_id == "j1")
        ).fetchone()
    assert row.status == "failed"
    assert row.error == "kaboom"
