from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.workspace import AppUser, UserMembership, Workspace
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead
from app.security.rbac import require_authenticated_user, require_viewer_role

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
AnyAuthenticatedUser = Annotated[AppUser, Depends(require_authenticated_user)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]


@router.get("/workspaces", response_model=list[WorkspaceRead], tags=["workspaces"])
def list_workspaces(db: DatabaseSession, current_user: AnyAuthenticatedUser) -> list[Workspace]:
    """Returns only workspaces `current_user` is a member of (Horizon5 Group A
    intentional behavior change, docs/plans/horizon5-implementation-plan.md
    §2.2: the previous `require_owner`-gated version returned every
    workspace unconditionally, which no longer makes sense once more than
    one user can exist)."""
    statement = (
        select(Workspace)
        .join(UserMembership, UserMembership.workspace_id == Workspace.id)
        .where(UserMembership.user_id == current_user.id)
        .order_by(Workspace.created_at)
    )
    return list(db.scalars(statement).all())


@router.post(
    "/workspaces",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
)
def create_workspace(
    payload: WorkspaceCreate, db: DatabaseSession, current_user: AnyAuthenticatedUser
) -> Workspace:
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    db.flush()
    db.add(UserMembership(workspace_id=workspace.id, user_id=current_user.id, role="owner"))
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead, tags=["workspaces"])
def get_workspace(workspace_id: UUID, db: DatabaseSession, _viewer: Viewer) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace
