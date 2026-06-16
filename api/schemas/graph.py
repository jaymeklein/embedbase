"""Response schemas for the tag-correlation graph endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """A graph node: a file (document) or a tag hub.

    Attributes:
        id: Document id for files, tag id for tag hubs.
        label: Display name (filename or tag name).
        kind: ``"file"`` or ``"tag"``.
        heat: Tag usage count for tag hubs; ``0`` for files.
        heat_pct: ``heat / max_heat`` in ``[0, 1]`` (``0`` when there is no heat).
        degree: Number of incident edges.
        meta: Kind-specific extras (file: ``file_type``; tag: ``color``).
    """

    id: str
    label: str
    kind: Literal["file", "tag"]
    heat: int
    heat_pct: float
    degree: int
    meta: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """A ``file → tag`` link."""

    source: str
    target: str


class GraphResponse(BaseModel):
    """The graph for a scope: nodes, edges, and a heat summary.

    Attributes:
        nodes: File nodes followed by tag hub nodes.
        edges: One ``file → tag`` edge per tag a file carries.
        tag_counts: Tag name → number of files carrying it.
        max_heat: Largest single tag usage count (``0`` when there are no tags).
    """

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    tag_counts: dict[str, int]
    max_heat: int
