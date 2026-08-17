from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.catalog import Exchange, ExchangeConnection, Market, Workspace
from app.schemas.catalog import (
    ExchangeConnectionRead,
    ExchangeRead,
    MarketRead,
    WorkspaceCreate,
    WorkspaceRead,
)

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get("/workspaces", response_model=list[WorkspaceRead], tags=["workspaces"])
def list_workspaces(db: DatabaseSession) -> list[Workspace]:
    return list(db.scalars(select(Workspace).order_by(Workspace.created_at)).all())


@router.post(
    "/workspaces",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
)
def create_workspace(payload: WorkspaceCreate, db: DatabaseSession) -> Workspace:
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead, tags=["workspaces"])
def get_workspace(workspace_id: UUID, db: DatabaseSession) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.get(
    "/workspaces/{workspace_id}/connections",
    response_model=list[ExchangeConnectionRead],
    tags=["connections"],
)
def list_workspace_connections(workspace_id: UUID, db: DatabaseSession) -> list[ExchangeConnection]:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    statement = (
        select(ExchangeConnection)
        .where(ExchangeConnection.workspace_id == workspace_id)
        .order_by(ExchangeConnection.created_at)
    )
    return list(db.scalars(statement).all())


@router.get("/exchanges", response_model=list[ExchangeRead], tags=["catalog"])
def list_exchanges(db: DatabaseSession) -> list[Exchange]:
    return list(db.scalars(select(Exchange).order_by(Exchange.code)).all())


@router.get("/markets", response_model=list[MarketRead], tags=["catalog"])
def list_markets(db: DatabaseSession) -> list[Market]:
    return list(db.scalars(select(Market).order_by(Market.code)).all())
