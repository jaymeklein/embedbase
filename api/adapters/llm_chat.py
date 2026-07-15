"""Shared chat-completion transport for LLM-backed adapters (the reranker).

One place for the three chat wire-formats so a new LLM-driven stage reuses the transport
instead of copying it:

- ``openai_compat`` — ``POST {base}/v1/chat/completions`` with a Bearer key.
- ``ollama``        — ``POST {base}/api/chat`` (no key; local by default).
- ``gemini``        — native ``POST {base}/v1beta/models/{model}:generateContent`` with an
  ``x-goog-api-key`` header, mirroring the embeddings Gemini adapter.

Each provider contributes two small pure functions — one that **builds** its request and one that
**parses** its reply — paired in ``_PROVIDERS``. ``chat_complete`` stays provider-agnostic: it looks
the pair up, builds, POSTs **once**, and parses. Request-building, the HTTP call, and reply-parsing
are therefore separated instead of interleaved, and a new backend is a new pair plus one registry
entry — the transport itself never changes. (That is also why giving an LLM-backed stage another
provider later is a config/UI change, not new transport code.)

Also home to :func:`list_ollama_models` — the ``/api/tags`` probe behind the config UI's model
pickers — which shares this module's ``OLLAMA_DEFAULT_URL`` with the chat path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple

from api.constants import GEMINI_API_BASE_URL

# Default endpoints mirror the embedding adapters' host.docker.internal convention.
OLLAMA_DEFAULT_URL = "http://host.docker.internal:11434"
OPENAI_DEFAULT_URL = "http://host.docker.internal:1234"
GEMINI_DEFAULT_URL = GEMINI_API_BASE_URL

# Local CPU inference of a small model takes ~1 min/call (cold start more), so the chat
# call needs a generous ceiling. Remote callers pass their own (shorter) timeout.
CHAT_TIMEOUT_SECONDS = 300.0


def post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    """POST ``payload`` as JSON, raise on a non-2xx status, and return the parsed body.

    The shared HTTP primitive for the remote LLM + hosted-rerank providers.
    """
    import httpx

    response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return dict(response.json())


class _Call(NamedTuple):
    """The provider-agnostic inputs a builder turns into a wire request."""

    model: str
    base_url: str | None
    key: str
    prompt: str
    temperature: float | None
    response_schema: dict[str, Any] | None


class _Request(NamedTuple):
    """A built HTTP request: where to POST, the JSON body, and the headers."""

    url: str
    payload: dict[str, Any]
    headers: dict[str, str]


def _messages(prompt: str) -> list[dict[str, str]]:
    """The single-user-message chat shape shared by the OpenAI + Ollama wire formats."""
    return [{"role": "user", "content": prompt}]


# --- openai_compat -----------------------------------------------------------------------------
def _openai_request(call: _Call) -> _Request:
    payload: dict[str, Any] = {"model": call.model, "messages": _messages(call.prompt)}
    if call.temperature is not None:
        payload["temperature"] = call.temperature
    if call.response_schema is not None:
        # strict mode requires additionalProperties:false and every property in `required`; we own
        # the schema, so add the closed-object key here rather than push it on callers.
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": True,
                "schema": {**call.response_schema, "additionalProperties": False},
            },
        }
    url = f"{call.base_url or OPENAI_DEFAULT_URL}/v1/chat/completions"
    return _Request(url, payload, {"Authorization": f"Bearer {call.key}"})


def _openai_reply(data: dict[str, Any]) -> str:
    return str(data["choices"][0]["message"]["content"])


# --- ollama ------------------------------------------------------------------------------------
def _ollama_request(call: _Call) -> _Request:
    payload: dict[str, Any] = {
        "model": call.model,
        "messages": _messages(call.prompt),
        "stream": False,
    }
    if call.temperature is not None:
        payload["options"] = {"temperature": call.temperature}
    if call.response_schema is not None:
        payload["format"] = call.response_schema  # Ollama takes a JSON Schema directly
    url = f"{call.base_url or OLLAMA_DEFAULT_URL}/api/chat"
    return _Request(url, payload, {})  # ollama is unauthenticated


def _ollama_reply(data: dict[str, Any]) -> str:
    return str(data["message"]["content"])


# --- gemini ------------------------------------------------------------------------------------
def _gemini_request(call: _Call) -> _Request:
    payload: dict[str, Any] = {"contents": [{"parts": [{"text": call.prompt}]}]}
    gen_config: dict[str, Any] = {}
    if call.temperature is not None:
        gen_config["temperature"] = call.temperature
    if call.response_schema is not None:
        gen_config["responseMimeType"] = "application/json"
        gen_config["responseSchema"] = call.response_schema
    if gen_config:
        payload["generationConfig"] = gen_config
    base = (call.base_url or GEMINI_DEFAULT_URL).rstrip("/")
    url = f"{base}/v1beta/models/{call.model}:generateContent"
    return _Request(url, payload, {"x-goog-api-key": call.key})


def _gemini_reply(data: dict[str, Any]) -> str:
    """Concatenate the text parts of Gemini's first candidate (``""`` when absent)."""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(str(part.get("text", "")) for part in parts)


_Builder = Callable[[_Call], _Request]
_Parser = Callable[[dict[str, Any]], str]

# One (build, parse) pair per provider. Extend by adding an entry — never by branching in
# ``chat_complete``.
_PROVIDERS: dict[str, tuple[_Builder, _Parser]] = {
    "openai_compat": (_openai_request, _openai_reply),
    "ollama": (_ollama_request, _ollama_reply),
    "gemini": (_gemini_request, _gemini_reply),
}


def chat_complete(
    *,
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    prompt: str,
    temperature: float | None = None,
    response_schema: dict[str, Any] | None = None,
    timeout: float = CHAT_TIMEOUT_SECONDS,
) -> str:
    """Send a single user-message prompt to a chat model and return the reply text.

    ``provider`` selects a ``(build, parse)`` pair from ``_PROVIDERS``; this function only
    orchestrates the shared flow — resolve the pair, build the request, POST once, parse the reply —
    so no provider's request-building or reply-parsing leaks into the transport.

    Args:
        provider: ``"openai_compat"`` | ``"ollama"`` | ``"gemini"`` — selects the wire format.
        model: The model id on that provider.
        base_url: Provider base URL; ``None`` uses the provider default.
        api_key: Bearer / API key (ignored by ``ollama``).
        prompt: The user message.
        temperature: Sent only when not ``None`` — so a caller that omits it keeps the
            provider's own default, while the reranker can pin ``0``.
        response_schema: When set, request provider-native **structured output** — the reply is
            constrained to JSON matching this JSON Schema. Maps to ``response_format`` (OpenAI),
            ``format`` (Ollama), and ``responseSchema`` + ``responseMimeType`` (Gemini), so a caller
            can read a field instead of scraping prose. Pass a plain (lowercase-type) JSON Schema;
            the OpenAI branch adds the ``strict``-mode keys itself.
        timeout: Per-request timeout in seconds.

    Returns:
        The model's reply text (the JSON document when ``response_schema`` is set).

    Raises:
        ValueError: The provider is not recognised.
        httpx.HTTPError: The request failed or returned a non-2xx status.
    """
    try:
        build, parse = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"Unknown LLM provider: {provider!r}") from None
    call = _Call(model, base_url, api_key or "", prompt, temperature, response_schema)
    request = build(call)
    return parse(post_json(request.url, request.payload, request.headers, timeout))


# --- ollama model listing ----------------------------------------------------------------------
def list_ollama_models(base_url: str | None) -> list[str]:
    """Return the names of models installed on the Ollama server, sorted.

    Queries Ollama's ``/api/tags`` endpoint (the default host when ``base_url``
    is blank). Raises ``httpx.HTTPError`` if the server is unreachable.
    """
    import httpx

    url = f"{base_url or OLLAMA_DEFAULT_URL}/api/tags"
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    models = response.json().get("models", [])
    return sorted(str(m["name"]) for m in models if isinstance(m, dict) and "name" in m)
