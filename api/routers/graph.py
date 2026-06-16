"""Graph router: the tag-correlation graph for a workspace or a collection.

Routing-only (Section 5): each handler resolves dependencies and delegates a
single call to api/services/graph.py. Master-key protected, like the other
management-plane routers.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.graph import GraphResponse
from api.services import graph as graph_svc
from api.services.auth import require_master

router = APIRouter(
    prefix="/workspaces/{ws_id}",
    tags=["graph"],
    dependencies=[Depends(require_master)],
)


@router.get("/graph", response_model=GraphResponse)
async def workspace_graph(
    ws_id: str,
    link_types: list[str] = Query(default=["tags"]),
    db: AsyncSession = Depends(get_db),
) -> GraphResponse:
    return await graph_svc.workspace_graph(ws_id, link_types, db)


@router.get("/collections/{col_id}/graph", response_model=GraphResponse)
async def collection_graph(
    ws_id: str,
    col_id: str,
    link_types: list[str] = Query(default=["tags"]),
    db: AsyncSession = Depends(get_db),
) -> GraphResponse:
    return await graph_svc.collection_graph(ws_id, col_id, link_types, db)
