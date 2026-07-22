"""Unit tests for the ingestion-queue event visibility filter (api/routers/ws.py::_visible).

The filter is the confidentiality boundary for the global ``ingestion-queue`` stream: a scoped
(non-admin) viewer must only ever receive events for collections in their readable set, and must
fail *closed* on anything it can't attribute to a collection.
"""

import json

from api.routers.ws import _visible

_ALLOWED = {"col_2"}


def test_visible_none_allowed_passes_everything():
    # allowed=None → master/admin (or a whole-collection topic): no filtering at all.
    assert _visible(json.dumps({"collection_id": "col_1"}), None) is True
    assert _visible("not even json", None) is True


def test_visible_keeps_in_scope_events_and_drops_others():
    assert _visible(json.dumps({"collection_id": "col_2"}), _ALLOWED) is True
    assert _visible(json.dumps({"collection_id": "col_1"}), _ALLOWED) is False


def test_visible_fails_closed_on_malformed_events():
    # A scoped viewer must never receive an event we can't attribute to a readable collection.
    assert _visible(json.dumps({"document_id": "d"}), _ALLOWED) is False  # no collection_id
    assert _visible("}{ not json", _ALLOWED) is False  # invalid JSON
    assert _visible(json.dumps("just-a-string"), _ALLOWED) is False  # valid JSON, non-dict
    assert _visible(json.dumps({"collection_id": ["x"]}), _ALLOWED) is False  # unhashable id
