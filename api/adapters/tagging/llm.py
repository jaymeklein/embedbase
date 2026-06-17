"""LLM-backed tag suggester for Ollama and OpenAI-compatible chat endpoints."""

from __future__ import annotations

import json
import re
from typing import Any

from api.models.tagging import TagSuggestion

_PROMPT = (
    "Suggest up to {n} short, lowercase topical tags for the text below. "
    "Prefer one or two words per tag. Avoid these existing tags: {existing}. "
    "For each tag include a confidence from 0 to 1 for how strongly it applies. "
    'Respond with ONLY a JSON array of objects like '
    '[{{"name": "example", "confidence": 0.9}}] and no prose.\n\nTEXT:\n{text}'
)

# Default endpoints mirror the embedding adapters' host.docker.internal convention.
_OLLAMA_DEFAULT = "http://host.docker.internal:11434"
_OPENAI_DEFAULT = "http://host.docker.internal:1234"

# ponytail: local CPU inference of a small model takes ~1min/call (cold start more),
# so the chat call needs a generous ceiling. Bump if larger models time out.
_CHAT_TIMEOUT_SECONDS = 300.0


def list_ollama_models(base_url: str | None) -> list[str]:
    """Return the names of models installed on the Ollama server, sorted.

    Queries Ollama's ``/api/tags`` endpoint (the default host when ``base_url``
    is blank). Raises ``httpx.HTTPError`` if the server is unreachable.
    """
    import httpx

    url = f"{base_url or _OLLAMA_DEFAULT}/api/tags"
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    models = response.json().get("models", [])
    return sorted(str(m["name"]) for m in models if isinstance(m, dict) and "name" in m)


def _build_prompt(text: str, existing_tags: list[str], max_tags: int) -> str:
    existing = ", ".join(existing_tags) if existing_tags else "(none)"
    return _PROMPT.format(n=max_tags, existing=existing, text=text[:6000])


def _clean_name(raw: str) -> str:
    """Lowercase, trim, and collapse whitespace in a candidate tag name."""
    return " ".join(raw.strip().lower().split())


def _clamp_confidence(value: Any) -> float:
    """Coerce a model-reported confidence into ``[0, 1]`` (0 when unparseable)."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_suggestions(raw: str, max_tags: int) -> list[tuple[str, float]]:
    """Parse a model reply into ranked ``(name, confidence)`` pairs.

    Prefers a JSON array of ``{"name", "confidence"}`` objects (real confidence);
    falls back to a plain list of names — JSON array or comma/newline delimited —
    with confidence derived from rank order.
    """
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    parsed: Any = None
    if match:
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            parsed = None

    pairs: list[tuple[str, float]] = []
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        pairs = [
            (_clean_name(str(obj.get("name", ""))), _clamp_confidence(obj.get("confidence")))
            for obj in parsed
        ]
    else:
        names = parsed if isinstance(parsed, list) else re.split(r"[,\n]", raw)
        pairs = [
            (_clean_name(str(item)), round(max(0.3, 0.95 - i * 0.07), 3))
            for i, item in enumerate(names)
        ]

    seen: dict[str, float] = {}
    for name, conf in pairs:
        if name and name not in seen:
            seen[name] = conf
    return list(seen.items())[:max_tags]


class LLMTagSuggester:
    """Generates tags via a chat completion (TagSuggester Protocol)."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str | None,
        api_key: str | None,
        max_tags: int = 8,
    ) -> None:
        self._provider = provider
        self._model = model
        self._base_url = base_url
        self._api_key = api_key or ""
        self._max_tags = max_tags

    def suggest(self, text: str, existing_tags: list[str]) -> list[TagSuggestion]:
        """Prompt the model and return suggestions with confidence, minus existing tags."""
        raw = self._complete(_build_prompt(text, existing_tags, self._max_tags))
        existing = {t.strip().lower() for t in existing_tags}
        return [
            TagSuggestion(name=name, confidence=conf)
            for name, conf in _parse_suggestions(raw, self._max_tags)
            if name not in existing
        ]

    def _complete(self, prompt: str) -> str:
        """POST the prompt to the configured chat endpoint and return the reply text."""
        messages = [{"role": "user", "content": prompt}]
        if self._provider == "openai_compat":
            url = f"{self._base_url or _OPENAI_DEFAULT}/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self._api_key}"}
            payload: dict[str, Any] = {"model": self._model, "messages": messages}
            data = self._post(url, payload, headers)
            return str(data["choices"][0]["message"]["content"])
        url = f"{self._base_url or _OLLAMA_DEFAULT}/api/chat"
        payload = {"model": self._model, "messages": messages, "stream": False}
        data = self._post(url, payload, {})
        return str(data["message"]["content"])

    @staticmethod
    def _post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        import httpx

        response = httpx.post(url, json=payload, headers=headers, timeout=_CHAT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return dict(response.json())
