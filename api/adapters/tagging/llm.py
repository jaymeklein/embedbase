"""LLM-backed tag suggester for Ollama and OpenAI-compatible chat endpoints."""

from __future__ import annotations

import json
import re
from typing import Any

from api.models.tagging import TagSuggestion

_PROMPT = (
    "Extract up to {n} short, lowercase topical tags for the text below. "
    "Prefer one or two words per tag. Avoid these existing tags: {existing}. "
    "Respond with ONLY a JSON array of strings.\n\nTEXT:\n{text}"
)

# Default endpoints mirror the embedding adapters' host.docker.internal convention.
_OLLAMA_DEFAULT = "http://host.docker.internal:11434"
_OPENAI_DEFAULT = "http://host.docker.internal:1234"


def _build_prompt(text: str, existing_tags: list[str], max_tags: int) -> str:
    existing = ", ".join(existing_tags) if existing_tags else "(none)"
    return _PROMPT.format(n=max_tags, existing=existing, text=text[:6000])


def _parse_tags(raw: str, max_tags: int) -> list[str]:
    """Parse a model reply (JSON array or delimited list) into clean tag names."""
    candidates: list[str] = []
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            candidates = [str(item) for item in json.loads(match.group(0))]
        except json.JSONDecodeError:
            candidates = []
    if not candidates:
        candidates = re.split(r"[,\n]", raw)
    seen: list[str] = []
    for cand in candidates:
        name = " ".join(cand.strip().lower().split())
        if name and name not in seen:
            seen.append(name)
    return seen[:max_tags]


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
        """Prompt the model and return ranked suggestions, excluding existing tags."""
        raw = self._complete(_build_prompt(text, existing_tags, self._max_tags))
        existing = {t.strip().lower() for t in existing_tags}
        names = [n for n in _parse_tags(raw, self._max_tags) if n not in existing]
        return [
            TagSuggestion(name=name, confidence=round(max(0.3, 0.95 - i * 0.07), 3))
            for i, name in enumerate(names)
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

        response = httpx.post(url, json=payload, headers=headers, timeout=60.0)
        response.raise_for_status()
        return dict(response.json())
