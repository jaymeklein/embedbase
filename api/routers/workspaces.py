from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.workspaces import WorkspaceCreate, WorkspaceUpdate
from api.services import workspaces as workspace_svc
from api.services.auth import require_master

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_master)],
)


@router.post("", status_code=201)
async def create_workspace(body: WorkspaceCreate, db: AsyncSession = Depends(get_db)):
    return await workspace_svc.create_workspace(
        name=body.name,
        description=body.description,
        color=body.color,
        icon=body.icon,
        db=db,
    )


@router.get("")
async def list_workspaces(db: AsyncSession = Depends(get_db)):
    return await workspace_svc.list_workspaces(db)


@router.get("/{ws_id}")
async def get_workspace(ws_id: str, db: AsyncSession = Depends(get_db)):
    return await workspace_svc.get_workspace(ws_id, db)


@router.patch("/{ws_id}")
async def update_workspace(
    ws_id: str, body: WorkspaceUpdate, db: AsyncSession = Depends(get_db)
):
    return await workspace_svc.update_workspace(ws_id, body, db)


@router.delete("/{ws_id}", status_code=204)
async def delete_workspace(ws_id: str, db: AsyncSession = Depends(get_db)):
    await workspace_svc.delete_workspace(ws_id, db)
