"""Shared embedding-adapter errors and HTTP-status handling."""

from __future__ import annotations

import httpx


class RateLimitError(Exception):
    """Raised by an embedding adapter when the provider signals a rate-limit / quota error
    (HTTP 429).

    A typed signal so the ingest worker can classify a pause **reliably** (``isinstance``)
    instead of string-matching arbitrary exception text, which risks both false negatives (a
    real limit missed) and false positives (an unrelated error mentioning "quota" retried
    forever). See ``worker.tasks._is_rate_limit``.
    """


# Bound the provider body in the error message, but keep it large enough to retain Gemini's
# nested ``retryDelay`` hint — which trails a long ``message`` + ``QuotaFailure`` details and
# which ``worker.tasks._retry_delay_seconds`` parses from this text to schedule the resume. A
# 500-char cap dropped it, forcing the fixed 60s fallback and premature retries against the
# still-exhausted quota. Provider error bodies are well under this bound.
_MAX_BODY_CHARS = 2000


def raise_for_status(response: httpx.Response) -> None:
    """Raise on a non-2xx embedding response, mapping HTTP 429 to :class:`RateLimitError`.

    Every httpx-based embedding adapter (Gemini / OpenAI-compatible / Ollama) routes its
    response through this one helper, so a provider rate limit surfaces as the *same* typed
    error regardless of backend and ``worker.tasks._is_rate_limit`` can rely on a single
    ``isinstance`` check. Other error statuses raise ``httpx.HTTPStatusError`` with the
    provider's response body included (it usually says *why*).
    """
    if not response.is_error:
        return
    message = f"embedding provider HTTP {response.status_code}: {response.text[:_MAX_BODY_CHARS]}"
    if response.status_code == 429:
        raise RateLimitError(message)
    raise httpx.HTTPStatusError(message, request=response.request, response=response)
