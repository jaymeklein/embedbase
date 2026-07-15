"""Unit tests for the document-listing filter specifications.

These check the spec *contract* — when a filter applies vs. skips — without a DB. The actual SQL
behaviour of each filter is covered end-to-end in test_documents_service.py.
"""

from api.models.document import DocumentListQuery
from api.services.document_filters import (
    FilenameSpec,
    FileTypeSpec,
    IndexedSpec,
    MaxSizeSpec,
    MinSizeSpec,
    StatusSpec,
    TagSpec,
    build_specs,
)
from api.services.filters import FilterSpec


async def _cond(spec: FilterSpec):
    """to_condition for a value-only spec (db is unused by everything except TagSpec)."""
    return await spec.to_condition(None)  # type: ignore[arg-type]


async def test_unset_filters_return_none_and_are_skipped() -> None:
    # A default query sets no filters, so every spec skips itself (returns None).
    for spec in build_specs(DocumentListQuery()):
        if isinstance(spec, TagSpec):
            continue  # its unset path is asserted separately (guards against a DB call)
        assert await _cond(spec) is None


async def test_set_filters_return_a_condition() -> None:
    q = DocumentListQuery(
        filename="a", file_type=".pdf", status="failed", indexed=True,
        embedding_model="ollama", storage_backend="local", min_size=1, max_size=9,
        created_after="2024-01-01", created_before="2024-02-01",
        updated_after="2024-01-01", updated_before="2024-02-01",
    )
    for spec in build_specs(q):
        if isinstance(spec, TagSpec):
            continue
        assert await _cond(spec) is not None


async def test_indexed_false_is_a_filter_not_a_skip() -> None:
    assert await _cond(IndexedSpec(None)) is None
    assert await _cond(IndexedSpec(True)) is not None
    assert await _cond(IndexedSpec(False)) is not None  # False = "not indexed", a real condition


async def test_size_zero_is_a_real_bound() -> None:
    # 0 is falsy but a legitimate size bound — the spec must use `is not None`, not truthiness.
    assert await _cond(MinSizeSpec(0)) is not None
    assert await _cond(MaxSizeSpec(0)) is not None
    assert await _cond(MinSizeSpec(None)) is None


async def test_empty_string_filters_are_skipped() -> None:
    # A bare `?filename=` binds "" — a falsy value the string specs must skip, not turn into a
    # match-all `ilike '%%'`. Guards the truthiness check against a future `is not None` "fix".
    assert await _cond(FilenameSpec("")) is None
    assert await _cond(FileTypeSpec("")) is None
    assert await _cond(StatusSpec("")) is None


async def test_tag_spec_skips_before_any_db_lookup_when_unset() -> None:
    # Unset tags return None *before* touching db (which is None here — a lookup would fail).
    assert await TagSpec(None).to_condition(None) is None  # type: ignore[arg-type]
    assert await TagSpec([]).to_condition(None) is None  # type: ignore[arg-type]


async def test_build_specs_reads_every_filter_field_exactly_once() -> None:
    # Give each filter field a distinct value, then check the multiset of spec values equals the
    # multiset of field values — proving every field is read by exactly one spec. Catches both a
    # forgotten spec (a field value missing) and a copy-pasted spec reading the wrong field (one
    # value duplicated, another missing) — neither of which a plain count would catch.
    q = DocumentListQuery(
        filename="fn", file_type="ft", status="st", indexed=True,
        embedding_model="em", storage_backend="sb", min_size=1, max_size=2,
        created_after="2024-01-01", created_before="2024-01-02",
        updated_after="2024-01-03", updated_before="2024-01-04", tag=["tg"],
    )
    filter_fields = set(DocumentListQuery.model_fields) - {"limit", "offset"}
    specs = build_specs(q)
    assert all(isinstance(s, FilterSpec) for s in specs)
    assert sorted(repr(s.value) for s in specs) == sorted(repr(getattr(q, f)) for f in filter_fields)
