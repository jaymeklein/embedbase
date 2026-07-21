"""Unit tests for the ingestion-queue (jobs) filter specifications.

Sibling of test_document_filters.py — checks the spec contract (apply vs. skip) without a DB; the
SQL behaviour of each job filter is covered end-to-end in test_jobs_service.py.
"""

from api.models.document import JobListQuery
from api.services.filters import FilterSpec
from api.services.job_filters import FilenameSpec, StatusSpec, build_specs


async def _cond(spec: FilterSpec):
    """Job specs never touch the DB, so db is unused here."""
    return await spec.to_condition(None)  # type: ignore[arg-type]


async def test_unset_filters_return_none_and_are_skipped() -> None:
    for spec in build_specs(JobListQuery()):
        assert await _cond(spec) is None


async def test_set_filters_return_a_condition() -> None:
    q = JobListQuery(
        status="failed", filename="a", file_type=".pdf", collection="c", collection_id="col_1",
        created_after="2024-01-01", created_before="2024-02-01",
    )
    for spec in build_specs(q):
        assert await _cond(spec) is not None


async def test_empty_string_filters_are_skipped() -> None:
    # A bare `?status=`/`?filename=` binds "" — falsy, so the spec skips it, not match-all.
    assert await _cond(StatusSpec("")) is None
    assert await _cond(FilenameSpec("")) is None


async def test_build_specs_reads_every_filter_field_exactly_once() -> None:
    # Distinct value per field, so the multiset of spec values must equal the field values —
    # catches a forgotten spec or a copy-pasted one reading the wrong field. (limit/offset are
    # pagination, not filters.)
    q = JobListQuery(
        status="st", filename="fn", file_type="ft", collection="co",
        created_after="2024-01-01", created_before="2024-01-02",
    )
    filter_fields = set(JobListQuery.model_fields) - {"limit", "offset"}
    specs = build_specs(q)
    assert all(isinstance(s, FilterSpec) for s in specs)
    assert sorted(repr(s.value) for s in specs) == sorted(repr(getattr(q, f)) for f in filter_fields)
