"""Local keyword tag suggester — no model server required.

A dependency-free TF-style extractor: it tokenizes the text, drops stopwords
and short tokens, scores unigrams and adjacent bigrams by frequency, and
returns the top terms as suggestions with a normalized confidence. Chosen over
KeyBERT/YAKE so the default backend runs fully offline with no extra packages.
"""

from __future__ import annotations

import re
from collections import Counter

from api.models.tagging import TagSuggestion

_TOKEN_RE = re.compile(r"[a-z][a-z0-9+#-]{2,}")

_STOPWORDS = frozenset(
    ["the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "to", "of", "in", "on", "at", "by", "with", "from", "into", "over", "under", "again", "further", "once", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "doing", "this", "that", "these", "those", "it", "its", "their", "there", "here", "what", "which", "who", "whom", "your", "you", "yours", "our", "ours", "they", "them", "his", "her", "she", "him", "not", "no", "nor", "so", "too", "very", "can", "will", "just", "should", "now", "about", "above", "below", "up", "down", "out", "off", "than", "more", "most", "some", "such", "only", "own", "same", "other", "as", "also", "we", "us", "i", "me", "my", "mine", "he", "get", "got", "like", "one", "two"]
)


def _tokens(text: str) -> list[str]:
    """Lowercase, extract word tokens, and drop stopwords/short tokens."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _score_terms(text: str) -> Counter[str]:
    """Score unigrams (weight 1) and adjacent bigrams (weight 2) by frequency."""
    tokens = _tokens(text)
    scores: Counter[str] = Counter(tokens)
    for left, right in zip(tokens, tokens[1:], strict=False):
        scores[f"{left} {right}"] += 2
    return scores


class KeywordTagSuggester:
    """Frequency-based local tag suggester (TagSuggester Protocol)."""

    def __init__(self, max_tags: int = 8) -> None:
        self._max_tags = max_tags

    def suggest(self, text: str, existing_tags: list[str]) -> list[TagSuggestion]:
        """Return up to ``max_tags`` frequency-ranked tags, excluding existing ones."""
        scores = _score_terms(text)
        if not scores:
            return []
        existing = {t.strip().lower() for t in existing_tags}
        top_score = scores.most_common(1)[0][1]
        out: list[TagSuggestion] = []
        for term, score in scores.most_common():
            if term in existing:
                continue
            out.append(TagSuggestion(name=term, confidence=round(score / top_score, 3)))
            if len(out) >= self._max_tags:
                break
        return out
