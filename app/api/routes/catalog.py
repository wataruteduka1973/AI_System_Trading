from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.catalog import Exchange, ExchangeConnection, Market, Workspace
from app.schemas.catalog import (
    ExchangeConnectionCreate,
    ExchangeConnectionRead,
    ExchangeRead,
    MarketRead,
    WorkspaceCreate,
    WorkspaceRead,
)
from app.security.auth import require_owner
from app.services.secrets import LocalEncryptedSecretStore, get_secret_store

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Owner = Annotated[str, Depends(require_owner)]
SecretStore = Annotated[LocalEncryptedSecretStore, Depends(get_secret_store)]


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


@router.get(
    "/workspaces/{workspace_id}/connections",
    response_model=list[ExchangeConnectionRead],
    tags=["connections"],
)
def list_workspace_connections(
    workspace_id: UUID, db: DatabaseSession, _owner: Owner
) -> list[ExchangeConnection]:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    statement = (
        select(ExchangeConnection)
        .where(ExchangeConnection.workspace_id == workspace_id)
        .order_by(ExchangeConnection.created_at)
    )
    return list(db.scalars(statement).all())


@router.post(
    "/workspaces/{workspace_id}/connections",
    response_model=ExchangeConnectionRead,
    status_code=status.HTTP_201_CREATED,
    tags=["connections"],
)
def create_exchange_connection(
    workspace_id: UUID,
    payload: ExchangeConnectionCreate,
    db: DatabaseSession,
    secret_store: SecretStore,
    _owner: Owner,
) -> ExchangeConnection:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    exchange = db.scalar(select(Exchange).where(Exchange.code == payload.exchange_code))
    if exchange is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exchange not found")

    secret_ref = secret_store.put(payload.revealed_credentials())
    connection = ExchangeConnection(
        workspace_id=workspace_id,
        exchange_id=exchange.id,
        label=payload.label,
        environment=payload.environment,
        api_base_url=str(payload.api_base_url),
        secret_ref=secret_ref,
        status="verifying",
        capabilities={},
    )
    try:
        db.add(connection)
        db.commit()
        db.refresh(connection)
    except Exception as exc:
        db.rollback()
        secret_store.delete(secret_ref)
        if isinstance(exc, IntegrityError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A connection with this label already exists",
            ) from exc
        raise
    return connection


@router.post(
    "/workspaces/{workspace_id}/connections/{connection_id}/disable",
    response_model=ExchangeConnectionRead,
    tags=["connections"],
)
def disable_exchange_connection(
    workspace_id: UUID,
    connection_id: UUID,
    db: DatabaseSession,
    _owner: Owner,
) -> ExchangeConnection:
    connection = db.scalar(
        select(ExchangeConnection).where(
            ExchangeConnection.id == connection_id,
            ExchangeConnection.workspace_id == workspace_id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    connection.status = "disabled"
    db.commit()
    db.refresh(connection)
    return connection


@router.get("/exchanges", response_model=list[ExchangeRead], tags=["catalog"])
def list_exchanges(db: DatabaseSession) -> list[Exchange]:
    return list(db.scalars(select(Exchange).order_by(Exchange.code)).all())


@router.get("/markets", response_model=list[MarketRead], tags=["catalog"])
def list_markets(db: DatabaseSession) -> list[Market]:
    return list(db.scalars(select(Market).order_by(Market.code)).all())
