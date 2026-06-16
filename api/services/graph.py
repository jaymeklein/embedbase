"""Tag-correlation graph: a bipartite file <-> tag hub graph.

The pure :func:`build_graph` transform turns pre-fetched file rows into nodes +
edges (no I/O, fully unit-testable). The :func:`workspace_graph` /
:func:`collection_graph` entry points do the I/O: gather active documents in
scope and their tags from the D6 join tables, then delegate. Files are
documents; tag hubs carry ``heat`` = the number of files using the tag.

ponytail: only the ``tags`` link type is implemented; ``language`` / ``file_type``
are a deferred follow-on (unknown link types are ignored). The graph is computed
live — caching is deferred until it measurably matters.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import documents as doc_t
from api.schemas.graph import GraphEdge, GraphNode, GraphResponse
from api.services import tags as tag_svc
from api.services.collections import require_collection
from api.services.workspaces import require_workspace


def _tag_counts(files: list[dict[str, Any]]) -> Counter[str]:
    """Count how many files carry each tag id."""
    counts: Counter[str] = Counter()
    for file in files:
        counts.update({tag["id"] for tag in file["tags"]})
    return counts


def _file_nodes(files: list[dict[str, Any]]) -> list[GraphNode]:
    """One node per file; degree is its tag count, heat is always zero."""
    return [
        GraphNode(
            id=file["id"],
            label=file["label"],
            kind="file",
            heat=0,
            heat_pct=0.0,
            degree=len(file["tags"]),
            meta={"file_type": file["file_type"]},
        )
        for file in files
    ]


def _tag_nodes(
    files: list[dict[str, Any]], counts: Counter[str], max_heat: int
) -> list[GraphNode]:
    """One hub node per distinct tag; heat (and degree) is its file usage count."""
    seen: dict[str, dict[str, Any]] = {}
    for file in files:
        for tag in file["tags"]:
            seen.setdefault(tag["id"], tag)
    return [
        GraphNode(
            id=tag_id,
            label=tag["name"],
            kind="tag",
            heat=counts[tag_id],
            heat_pct=counts[tag_id] / max_heat if max_heat else 0.0,
            degree=counts[tag_id],
            meta={"color": tag["color"]},
        )
        for tag_id, tag in seen.items()
    ]


def _edges(files: list[dict[str, Any]]) -> list[GraphEdge]:
    """A ``file -> tag`` edge for every tag each file carries."""
    return [
        GraphEdge(source=file["id"], target=tag["id"])
        for file in files
        for tag in file["tags"]
    ]


def build_graph(files: list[dict[str, Any]], link_types: list[str]) -> GraphResponse:
    """Assemble the file <-> tag graph from pre-fetched file rows.

    Args:
        files: One ``{id, label, file_type, tags: [{id, name, color}]}`` per file.
        link_types: Requested link kinds. Only ``"tags"`` is implemented; when it
            is absent the response carries file nodes only.

    Returns:
        File nodes plus tag hubs, ``file -> tag`` edges, per-tag-name counts, and
        the maximum single-tag usage count.
    """
    if "tags" not in link_types:
        return GraphResponse(nodes=_file_nodes(files), edges=[], tag_counts={}, max_heat=0)
    counts = _tag_counts(files)
    max_heat = max(counts.values(), default=0)
    nodes = _file_nodes(files) + _tag_nodes(files, counts, max_heat)
    tag_counts = {tag["name"]: counts[tag["id"]] for file in files for tag in file["tags"]}
    return GraphResponse(
        nodes=nodes, edges=_edges(files), tag_counts=tag_counts, max_heat=max_heat
    )


async def _files_with_tags(rows: Sequence[Any], db: AsyncSession) -> list[dict[str, Any]]:
    """Shape document rows into graph file items, attaching each document's tags."""
    tag_map = await tag_svc.tags_by_entity("document", [row.id for row in rows], db)
    return [
        {
            "id": row.id,
            "label": row.filename,
            "file_type": row.file_type,
            "tags": tag_map.get(row.id, []),
        }
        for row in rows
    ]


async def workspace_graph(
    ws_id: str, link_types: list[str], db: AsyncSession
) -> GraphResponse:
    """Build the graph over every active document in a workspace.

    Raises:
        HTTPException: 404 when the workspace is absent.
    """
    await require_workspace(ws_id, db)
    rows = (
        await db.execute(
            select(doc_t.c.id, doc_t.c.filename, doc_t.c.file_type)
            .select_from(doc_t.join(col_t, col_t.c.id == doc_t.c.collection_id))
            .where(col_t.c.workspace_id == ws_id, doc_t.c.status.is_(None))
            .order_by(doc_t.c.filename)
        )
    ).fetchall()
    return build_graph(await _files_with_tags(rows, db), link_types)


async def collection_graph(
    ws_id: str, col_id: str, link_types: list[str], db: AsyncSession
) -> GraphResponse:
    """Build the graph over a single collection's active documents.

    Raises:
        HTTPException: 404 when the collection is absent from the workspace.
    """
    await require_collection(ws_id, col_id, db)
    rows = (
        await db.execute(
            select(doc_t.c.id, doc_t.c.filename, doc_t.c.file_type)
            .where(doc_t.c.collection_id == col_id, doc_t.c.status.is_(None))
            .order_by(doc_t.c.filename)
        )
    ).fetchall()
    return build_graph(await _files_with_tags(rows, db), link_types)
