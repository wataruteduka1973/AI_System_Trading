from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.workspace import Workspace
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead
from app.security.auth import require_owner

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Owner = Annotated[str, Depends(require_owner)]


@router.get("/workspaces", response_model=list[WorkspaceRead], tags=["workspaces"])
def list_workspaces(db: DatabaseSession, _owner: Owner) -> list[Workspace]:
    return list(db.scalars(select(Workspace).order_by(Workspace.created_at)).all())


@router.post(
    "/workspaces",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
)
def create_workspace(payload: WorkspaceCreate, db: DatabaseSession, _owner: Owner) -> Workspace:
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead, tags=["workspaces"])
def get_workspace(workspace_id: UUID, db: DatabaseSession, _owner: Owner) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace
