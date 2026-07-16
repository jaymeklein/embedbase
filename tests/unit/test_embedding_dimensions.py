"""Unit tests for the shared, rate-limit-tolerant embedding-dimension helpers.

``resolve_dimensions`` / ``same_embedding_shape`` back both config-apply call sites (the API's
``config_service._build_adapters`` and the worker's ``tasks.reload_adapters``), so the size a
throttled provider can't be probed for is reused from the live store rather than failing the save.
"""

from __future__ import annotations

import pytest

from api.adapters.embeddings import resolve_dimensions, same_embedding_shape
from api.adapters.embeddings.errors import RateLimitError
from api.models.config import EmbeddingConfig


class _Embed:
    """Embedding adapter whose dimension probe returns a fixed size."""

    def __init__(self, dims: int) -> None:
        self._dims = dims

    @property
    def dimensions(self) -> int:
        return self._dims


class _RateLimitedEmbed:
    """Embedding adapter whose dimension probe hits the provider rate limit (HTTP 429)."""

    @property
    def dimensions(self) -> int:
        raise RateLimitError("embedding provider HTTP 429: quota exceeded")


def _cfg(**over: object) -> EmbeddingConfig:
    base: dict = {"provider": "gemini", "model": "gemini-embedding-2", "api_key": "k"}
    base.update(over)
    return EmbeddingConfig(**base)


# ── same_embedding_shape ──────────────────────────────────────────────────────


def test_same_shape_ignores_non_dimension_fields():
    # Rate limit, API key, batch size, and concurrency don't affect the vector size.
    a = _cfg(api_key="k1", max_rpm=50, batch_size=16, concurrency=4)
    b = _cfg(api_key="k2", max_rpm=90, batch_size=32, concurrency=8)
    assert same_embedding_shape(a, b)


@pytest.mark.parametrize(
    "over",
    [
        {"provider": "openai_compat"},
        {"model": "gemini-embedding-99"},
        {"base_url": "http://other:1234"},
        {"output_dimensionality": 1536},
    ],
)
def test_different_shape_when_a_dimension_field_changes(over):
    assert not same_embedding_shape(_cfg(), _cfg(**over))


# ── resolve_dimensions ────────────────────────────────────────────────────────


def test_probes_when_not_rate_limited():
    # Happy path: the provider answers, so the freshly probed size wins and prior state is ignored
    # (this is what self-heals a stale boot-fallback size once the provider is reachable again).
    assert resolve_dimensions(_Embed(3072), _cfg(), prior_config=_cfg(), prior_dimensions=768) == 3072


def test_reuses_prior_dimension_on_429_when_shape_unchanged():
    # 429 during the probe + unchanged model → reuse the prior store's known size instead of failing.
    dims = resolve_dimensions(
        _RateLimitedEmbed(), _cfg(max_rpm=90), prior_config=_cfg(max_rpm=50), prior_dimensions=768
    )
    assert dims == 768


def test_reraises_on_429_when_model_changed():
    # 429 + a real model change → the new size genuinely can't be known; surface the limit.
    with pytest.raises(RateLimitError):
        resolve_dimensions(
            _RateLimitedEmbed(),
            _cfg(model="gemini-embedding-99"),
            prior_config=_cfg(),
            prior_dimensions=768,
        )


def test_reraises_on_429_when_no_prior_dimension():
    # 429 with nothing to reuse (no live store yet) → can't size it; surface the limit.
    with pytest.raises(RateLimitError):
        resolve_dimensions(_RateLimitedEmbed(), _cfg(), prior_config=_cfg(), prior_dimensions=None)


def test_reraises_on_429_when_no_prior_config():
    with pytest.raises(RateLimitError):
        resolve_dimensions(_RateLimitedEmbed(), _cfg(), prior_config=None, prior_dimensions=768)
