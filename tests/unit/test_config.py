"""Unit tests for AppConfig validation and unknown-key warnings."""

import warnings

import pytest
from pydantic import ValidationError

from api.constants import POSTGRES_PORT
from api.models.config import AppConfig


def test_defaults_are_populated():
    cfg = AppConfig()
    assert cfg.embedding.provider == "ollama"
    assert cfg.embedding.model == "embeddinggemma"
    assert cfg.embedding.batch_size == 32
    assert cfg.vector_store.host == "postgres"
    assert cfg.vector_store.port == POSTGRES_PORT
    assert cfg.chunking.sliding_window.max_tokens == 512
    assert cfg.chunking.sliding_window.overlap_tokens == 64
    assert cfg.search.hybrid_default_alpha == 0.7
    assert cfg.mcp.rate_limit_rpm == 60


@pytest.mark.parametrize("rpm", [0, -60])
def test_mcp_rate_limit_rejects_values_below_one(rpm):
    """The MCP limiter re-reads rate_limit_rpm per request, so a 0/negative value would brick the
    endpoint the instant it is saved — 0 denies everything, and a negative rate accrues token debt
    that outlives the correction (the bucket is never rebuilt). Reject it at the boundary: a bad
    PUT /config is a 422 instead of a live lockout."""
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"mcp": {"rate_limit_rpm": rpm}})


def test_mcp_rate_limit_accepts_one():
    assert AppConfig.model_validate({"mcp": {"rate_limit_rpm": 1}}).mcp.rate_limit_rpm == 1


def test_nested_override_applies():
    cfg = AppConfig.model_validate(
        {
            "embedding": {"model": "custom-model", "batch_size": 8},
            "chunking": {"sliding_window": {"max_tokens": 256}},
        }
    )
    assert cfg.embedding.model == "custom-model"
    assert cfg.embedding.batch_size == 8
    assert cfg.chunking.sliding_window.max_tokens == 256
    # Untouched nested defaults are preserved.
    assert cfg.chunking.sliding_window.overlap_tokens == 64


def test_unknown_top_level_key_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        AppConfig.model_validate({"embedding": {}, "bogus_key": 1})
    messages = [str(w.message) for w in caught]
    assert any("bogus_key" in m for m in messages)


def test_unknown_nested_key_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        AppConfig.model_validate({"embedding": {"not_a_field": "x"}})
    messages = [str(w.message) for w in caught]
    assert any("embedding.not_a_field" in m for m in messages)


def test_known_keys_do_not_warn():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        AppConfig.model_validate({"embedding": {"model": "ok"}})
    messages = [str(w.message) for w in caught]
    assert not any("unknown key" in m for m in messages)
