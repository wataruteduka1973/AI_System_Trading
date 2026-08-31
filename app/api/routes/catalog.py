from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Table, delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.connections.application import (
    BinanceVerificationResult,
    ConnectionVerificationError,
    OandaVerificationResult,
    VerifyConnectionUseCase,
)
from app.db.session import get_db
from app.exchanges.binance import (
    BinanceSpotTestnetClient,
    get_binance_spot_testnet_client,
)
from app.exchanges.oanda import (
    OandaPracticeClient,
    get_oanda_practice_client,
)
from app.models.catalog import (
    AuditLog,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Market,
    Workspace,
    WorkspaceAccountSelection,
)
from app.schemas.catalog import (
    BinanceAccountRead,
    BinanceVerificationRead,
    ExchangeConnectionCreate,
    ExchangeConnectionRead,
    ExchangeCredentialsUpdate,
    ExchangeRead,
    MarketRead,
    OandaAccountRead,
    OandaVerificationRead,
    WorkspaceAccountRead,
    WorkspaceAccountSelectionRead,
    WorkspaceAccountSelectionUpdate,
    WorkspaceCreate,
    WorkspaceRead,
)
from app.security.auth import require_owner
from app.services.secrets import LocalEncryptedSecretStore, get_secret_store

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Owner = Annotated[str, Depends(require_owner)]
SecretStore = Annotated[LocalEncryptedSecretStore, Depends(get_secret_store)]
OandaClient = Annotated[OandaPracticeClient, Depends(get_oanda_practice_client)]
BinanceClient = Annotated[BinanceSpotTestnetClient, Depends(get_binance_spot_testnet_client)]


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
        credentials_updated_at=datetime.now(UTC),
        verification_outcome="not_verified",
        capabilities={},
    )
    try:
        db.add(connection)
        db.flush()
        _add_audit(
            db,
            workspace_id,
            "connection.credentials_created",
            connection.id,
            after={"credentials_status": "saved", "verification_outcome": "not_verified"},
        )
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
    previous_status = connection.status
    connection.status = "disabled"
    selection_table = WorkspaceAccountSelection.__table__
    assert isinstance(selection_table, Table)
    db.execute(
        delete(selection_table).where(
            WorkspaceAccountSelection.external_account_id.in_(
                select(ExternalAccount.id).where(ExternalAccount.connection_id == connection.id)
            )
        )
    )
    _add_audit(
        db,
        workspace_id,
        "connection.disabled",
        connection.id,
        before={"status": previous_status},
        after={"status": "disabled"},
    )
    try:
        db.commit()
        db.refresh(connection)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Connection could not be disabled",
        ) from exc
    return connection


@router.delete(
    "/workspaces/{workspace_id}/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["connections"],
)
def delete_exchange_connection(
    workspace_id: UUID,
    connection_id: UUID,
    db: DatabaseSession,
    secret_store: SecretStore,
    _owner: Owner,
) -> None:
    connection = _get_connection(db, workspace_id, connection_id)
    if connection.status not in {"disabled", "invalid", "revoked", "pending_credentials"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Disable a verified or active connection before deleting it",
        )
    secret_ref = connection.secret_ref
    _add_audit(
        db,
        workspace_id,
        "connection.deleted",
        connection.id,
        before={"status": connection.status, "credentials_status": connection.credentials_status},
        after=None,
    )
    db.delete(connection)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Connection could not be deleted",
        ) from exc
    if secret_ref:
        secret_store.delete(secret_ref)


@router.put(
    "/workspaces/{workspace_id}/connections/{connection_id}/credentials",
    response_model=OandaVerificationRead | BinanceVerificationRead,
    tags=["connections"],
)
async def update_connection_credentials_and_verify(
    workspace_id: UUID,
    connection_id: UUID,
    payload: ExchangeCredentialsUpdate,
    db: DatabaseSession,
    secret_store: SecretStore,
    oanda_client: OandaClient,
    binance_client: BinanceClient,
    _owner: Owner,
) -> OandaVerificationRead | BinanceVerificationRead:
    connection = db.scalar(
        select(ExchangeConnection).where(
            ExchangeConnection.id == connection_id,
            ExchangeConnection.workspace_id == workspace_id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    exchange = db.get(Exchange, connection.exchange_id)
    if exchange is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Exchange is missing")

    credentials = payload.revealed_credentials()
    _validate_connection_credentials(exchange.code, credentials)
    new_secret_ref = secret_store.put(credentials)
    old_secret_ref = connection.secret_ref
    connection.secret_ref = new_secret_ref
    connection.status = "verifying"
    connection.credentials_updated_at = datetime.now(UTC)
    connection.verification_outcome = "not_verified"
    _add_audit(
        db,
        workspace_id,
        "connection.credentials_updated",
        connection.id,
        before={"credentials_status": "saved" if old_secret_ref else "missing"},
        after={"credentials_status": "saved", "verification_outcome": "not_verified"},
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        secret_store.delete(new_secret_ref)
        raise
    if old_secret_ref is not None:
        secret_store.delete(old_secret_ref)

    try:
        return await verify_exchange_connection(
            workspace_id=workspace_id,
            connection_id=connection_id,
            db=db,
            secret_store=secret_store,
            oanda_client=oanda_client,
            binance_client=binance_client,
            _owner=_owner,
        )
    except HTTPException as exc:
        if exc.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_502_BAD_GATEWAY}:
            raise HTTPException(
                status_code=exc.status_code,
                detail=f"Credentials were updated, but verification failed: {exc.detail}",
            ) from exc
        raise


@router.post(
    "/workspaces/{workspace_id}/connections/{connection_id}/verify",
    response_model=OandaVerificationRead | BinanceVerificationRead,
    tags=["connections"],
)
async def verify_exchange_connection(
    workspace_id: UUID,
    connection_id: UUID,
    db: DatabaseSession,
    secret_store: SecretStore,
    oanda_client: OandaClient,
    binance_client: BinanceClient,
    _owner: Owner,
) -> OandaVerificationRead | BinanceVerificationRead:
    use_case = VerifyConnectionUseCase(
        db=db,
        secret_store=secret_store,
        oanda_client=oanda_client,
        binance_client=binance_client,
    )
    try:
        result = await use_case.execute(workspace_id, connection_id)
    except ConnectionVerificationError as exc:
        raise _verification_http_error(exc) from exc
    return _verification_response(result)


def _verification_http_error(exc: ConnectionVerificationError) -> HTTPException:
    status_by_code = {
        "connection_not_found": status.HTTP_404_NOT_FOUND,
        "exchange_missing": status.HTTP_409_CONFLICT,
        "unsupported_environment": status.HTTP_400_BAD_REQUEST,
        "credentials_missing": status.HTTP_409_CONFLICT,
        "credentials_unavailable": status.HTTP_409_CONFLICT,
        "credentials_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "authentication_failed": status.HTTP_401_UNAUTHORIZED,
        "communication_failed": status.HTTP_502_BAD_GATEWAY,
        "result_persistence_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return HTTPException(status_code=status_by_code[exc.code], detail=str(exc))


def _verification_response(
    result: OandaVerificationResult | BinanceVerificationResult,
) -> OandaVerificationRead | BinanceVerificationRead:
    if isinstance(result, OandaVerificationResult):
        return OandaVerificationRead(
            connection_id=result.connection_id,
            status=result.status,
            accounts=[
                OandaAccountRead(
                    account_ref_masked=account.account_ref_masked,
                    alias=account.alias,
                    currency=account.currency,
                    hedging_enabled=account.hedging_enabled,
                    margin_rate=account.margin_rate,
                    gslo_mode=account.gslo_mode,
                    usd_jpy_tradeable=account.usd_jpy_tradeable,
                )
                for account in result.accounts
            ],
        )
    return BinanceVerificationRead(
        connection_id=result.connection_id,
        status=result.status,
        accounts=[
            BinanceAccountRead(
                account_ref_masked=account.account_ref_masked,
                account_type=account.account_type,
                permissions=account.permissions,
                can_trade=account.can_trade,
                can_deposit=account.can_deposit,
                can_withdraw=account.can_withdraw,
                nonzero_asset_count=account.nonzero_asset_count,
                btc_jpy_tradeable=account.btc_jpy_tradeable,
            )
            for account in result.accounts
        ],
    )


@router.get(
    "/workspaces/{workspace_id}/accounts",
    response_model=list[WorkspaceAccountRead],
    tags=["accounts"],
)
def list_workspace_accounts(
    workspace_id: UUID, db: DatabaseSession, _owner: Owner
) -> list[WorkspaceAccountRead]:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    rows = db.execute(
        select(ExternalAccount, ExchangeConnection, Exchange, WorkspaceAccountSelection)
        .join(ExchangeConnection, ExternalAccount.connection_id == ExchangeConnection.id)
        .join(Exchange, ExchangeConnection.exchange_id == Exchange.id)
        .outerjoin(
            WorkspaceAccountSelection,
            (WorkspaceAccountSelection.workspace_id == workspace_id)
            & (WorkspaceAccountSelection.external_account_id == ExternalAccount.id),
        )
        .where(ExchangeConnection.workspace_id == workspace_id)
        .order_by(Exchange.code, ExternalAccount.created_at)
    ).all()
    return [
        WorkspaceAccountRead(
            id=account.id,
            connection_id=connection.id,
            exchange_id=exchange.id,
            exchange_code=exchange.code,
            connection_label=connection.label,
            connection_status=connection.status,
            account_ref_masked=account.external_account_ref_masked,
            alias=account.alias,
            environment=account.environment,
            currency=account.currency,
            status=account.status,
            selected=selection is not None,
        )
        for account, connection, exchange, selection in rows
    ]


@router.put(
    "/workspaces/{workspace_id}/account-selections/{exchange_code}",
    response_model=WorkspaceAccountSelectionRead,
    tags=["accounts"],
)
def select_workspace_account(
    workspace_id: UUID,
    exchange_code: str,
    payload: WorkspaceAccountSelectionUpdate,
    db: DatabaseSession,
    _owner: Owner,
) -> WorkspaceAccountSelection:
    row = db.execute(
        select(ExternalAccount, ExchangeConnection, Exchange)
        .join(ExchangeConnection, ExternalAccount.connection_id == ExchangeConnection.id)
        .join(Exchange, ExchangeConnection.exchange_id == Exchange.id)
        .where(
            ExternalAccount.id == payload.external_account_id,
            ExchangeConnection.workspace_id == workspace_id,
            Exchange.code == exchange_code,
            ExchangeConnection.status == "verified",
            ExternalAccount.status == "active",
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an active account from a verified workspace connection can be selected",
        )
    account, _connection, exchange = row
    selection = db.scalar(
        select(WorkspaceAccountSelection).where(
            WorkspaceAccountSelection.workspace_id == workspace_id,
            WorkspaceAccountSelection.exchange_id == exchange.id,
        )
    )
    previous_account_id = selection.external_account_id if selection else None
    now = datetime.now(UTC)
    if selection is None:
        selection = WorkspaceAccountSelection(
            workspace_id=workspace_id,
            exchange_id=exchange.id,
            external_account_id=account.id,
            selected_at=now,
            updated_at=now,
        )
        db.add(selection)
    else:
        selection.external_account_id = account.id
        selection.selected_at = now
        selection.updated_at = now
    _add_audit(
        db,
        workspace_id,
        "workspace.account_selected",
        account.id,
        before={"external_account_id": str(previous_account_id)} if previous_account_id else None,
        after={"exchange_code": exchange.code, "external_account_id": str(account.id)},
    )
    db.commit()
    db.refresh(selection)
    return selection


def _get_connection(db: Session, workspace_id: UUID, connection_id: UUID) -> ExchangeConnection:
    connection = db.scalar(
        select(ExchangeConnection).where(
            ExchangeConnection.id == connection_id,
            ExchangeConnection.workspace_id == workspace_id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return connection


def _add_audit(
    db: Session,
    workspace_id: UUID,
    action: str,
    resource_id: UUID,
    *,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=None,
            action=action,
            resource_type="exchange_connection",
            resource_id=resource_id,
            before_data=before,
            after_data=after,
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )


def _validate_connection_credentials(exchange_code: str, credentials: dict[str, str]) -> None:
    required_keys = {
        "oanda": {"token"},
        "binance": {"api_key", "secret_key"},
    }.get(exchange_code)
    if required_keys is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credential updates are not supported for this exchange",
        )
    if set(credentials) != required_keys or any(not credentials[key] for key in required_keys):
        expected = ", ".join(sorted(required_keys))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{exchange_code.title()} credentials must contain only: {expected}",
        )


@router.get("/exchanges", response_model=list[ExchangeRead], tags=["catalog"])
def list_exchanges(db: DatabaseSession) -> list[Exchange]:
    return list(db.scalars(select(Exchange).order_by(Exchange.code)).all())


@router.get("/markets", response_model=list[MarketRead], tags=["catalog"])
def list_markets(db: DatabaseSession) -> list[Market]:
    return list(db.scalars(select(Market).order_by(Market.code)).all())
