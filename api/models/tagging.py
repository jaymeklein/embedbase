"""Models for AI-assisted tag suggestion."""

from pydantic import BaseModel


class TagSuggestion(BaseModel):
    """A single proposed tag with a confidence in ``[0, 1]``.

    Suggestions are ephemeral: they are returned to the client for review and
    are only persisted if the user applies them through the assign endpoints.
    """

    name: str
    confidence: float
