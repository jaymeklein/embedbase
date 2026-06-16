"""Tag-suggester registry — backend selected via config, no router changes."""

from typing import TYPE_CHECKING

from api.adapters.base import TagSuggester

if TYPE_CHECKING:
    from api.models.config import TaggingConfig


def get_tag_suggester(config: "TaggingConfig") -> TagSuggester:
    """Resolve and instantiate the configured tag suggester."""
    suggester = config.suggester
    backend = suggester.backend

    if backend == "keyword":
        from api.adapters.tagging.keyword import KeywordTagSuggester
        return KeywordTagSuggester(max_tags=suggester.max_tags)

    if backend == "llm":
        from api.adapters.tagging.llm import LLMTagSuggester
        return LLMTagSuggester(
            provider=suggester.provider,
            model=suggester.model,
            base_url=suggester.base_url,
            api_key=suggester.api_key,
            max_tags=suggester.max_tags,
        )

    raise ValueError(f"Unknown tag suggester backend: {backend!r}")
