"""Request/response schemas for the workspaces endpoints."""

from pydantic import BaseModel


class WorkspaceCreate(BaseModel):
    """Body for POST /workspaces."""

    name: str
    description: str = ""
    color: str = "#6366f1"
    icon: str = "folder"


class WorkspaceUpdate(BaseModel):
    """Body for PATCH /workspaces/{ws_id}."""

    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None
