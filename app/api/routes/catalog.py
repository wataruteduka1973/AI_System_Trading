import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.exchanges.binance import (
    BinanceApiError,
    BinanceAuthenticationError,
    BinanceSpotTestnetClient,
    get_binance_spot_testnet_client,
    mask_api_key,
)
from app.exchanges.oanda import (
    OandaApiError,
    OandaAuthenticationError,
    OandaPracticeClient,
    get_oanda_practice_client,
    mask_account_id,
)
from app.models.catalog import Exchange, ExchangeConnection, ExternalAccount, Market, Workspace
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
    supported_connection = (exchange.code == "oanda" and connection.environment == "practice") or (
        exchange.code == "binance" and connection.environment == "testnet"
    )
    if not supported_connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only OANDA practice and Binance Spot Testnet connections can be verified",
        )
    if connection.secret_ref is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Connection credentials are missing"
        )
    try:
        credentials = secret_store.get(connection.secret_ref)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connection credentials cannot be loaded",
        ) from exc

    if exchange.code == "binance":
        return await _verify_binance_connection(
            connection, credentials, db, secret_store, binance_client
        )

    token = credentials.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="OANDA credentials must include a token",
        )

    connection.status = "verifying"
    db.commit()
    try:
        accounts = await oanda_client.verify_and_list_accounts(connection.api_base_url, token)
    except OandaAuthenticationError as exc:
        connection.status = "invalid"
        connection.last_verified_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except OandaApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    now = datetime.now(UTC)
    response_accounts: list[OandaAccountRead] = []
    for account in accounts:
        account_hash = hashlib.sha256(account.account_id.encode("utf-8")).hexdigest()
        external_account = db.scalar(
            select(ExternalAccount).where(
                ExternalAccount.connection_id == connection.id,
                ExternalAccount.external_account_ref_hash == account_hash,
            )
        )
        if external_account is None:
            external_account = ExternalAccount(
                connection_id=connection.id,
                external_account_ref_hash=account_hash,
                external_account_ref_encrypted=secret_store.encrypt_text(account.account_id),
                external_account_ref_masked=mask_account_id(account.account_id),
                environment="practice",
                currency=account.currency,
            )
            db.add(external_account)
        external_account.alias = account.alias
        external_account.hedging_enabled = account.hedging_enabled
        external_account.margin_rate = account.margin_rate
        external_account.gslo_mode = account.gslo_mode
        external_account.capabilities = {"usd_jpy_tradeable": account.usd_jpy_tradeable}
        external_account.status = "active"
        external_account.synced_at = now
        response_accounts.append(
            OandaAccountRead(
                account_ref_masked=external_account.external_account_ref_masked,
                alias=account.alias,
                currency=account.currency,
                hedging_enabled=account.hedging_enabled,
                margin_rate=str(account.margin_rate) if account.margin_rate is not None else None,
                gslo_mode=account.gslo_mode,
                usd_jpy_tradeable=account.usd_jpy_tradeable,
            )
        )

    connection.status = "verified"
    connection.last_verified_at = now
    connection.capabilities = {
        "account_count": len(accounts),
        "usd_jpy_tradeable": any(account.usd_jpy_tradeable for account in accounts),
        "read_only_verified": True,
    }
    db.commit()
    return OandaVerificationRead(
        connection_id=connection.id,
        status=connection.status,
        accounts=response_accounts,
    )


async def _verify_binance_connection(
    connection: ExchangeConnection,
    credentials: dict[str, str],
    db: Session,
    secret_store: LocalEncryptedSecretStore,
    binance_client: BinanceSpotTestnetClient,
) -> BinanceVerificationRead:
    api_key = credentials.get("api_key")
    secret_key = credentials.get("secret_key")
    if not api_key or not secret_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Binance credentials must include api_key and secret_key",
        )

    connection.status = "verifying"
    db.commit()
    try:
        account = await binance_client.verify_account(
            connection.api_base_url, api_key, secret_key
        )
    except BinanceAuthenticationError as exc:
        connection.status = "invalid"
        connection.last_verified_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except BinanceApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    now = datetime.now(UTC)
    account_hash = hashlib.sha256(account.account_ref.encode("utf-8")).hexdigest()
    external_account = db.scalar(
        select(ExternalAccount).where(
            ExternalAccount.connection_id == connection.id,
            ExternalAccount.external_account_ref_hash == account_hash,
        )
    )
    if external_account is None:
        external_account = ExternalAccount(
            connection_id=connection.id,
            external_account_ref_hash=account_hash,
            external_account_ref_encrypted=secret_store.encrypt_text(account.account_ref),
            external_account_ref_masked=mask_api_key(account.account_ref),
            environment="testnet",
            currency="MULTI",
        )
        db.add(external_account)
    external_account.alias = "Binance Spot Testnet"
    external_account.capabilities = {
        "account_type": account.account_type,
        "permissions": list(account.permissions),
        "can_trade": account.can_trade,
        "can_deposit": account.can_deposit,
        "can_withdraw": account.can_withdraw,
        "nonzero_asset_count": account.nonzero_asset_count,
        "btc_jpy_tradeable": account.btc_jpy_tradeable,
    }
    external_account.status = "active"
    external_account.synced_at = now

    connection.status = "verified"
    connection.last_verified_at = now
    connection.capabilities = {
        **external_account.capabilities,
        "account_count": 1,
        "read_only_verified": True,
    }
    db.commit()
    return BinanceVerificationRead(
        connection_id=connection.id,
        status=connection.status,
        accounts=[
            BinanceAccountRead(
                account_ref_masked=external_account.external_account_ref_masked,
                account_type=account.account_type,
                permissions=list(account.permissions),
                can_trade=account.can_trade,
                can_deposit=account.can_deposit,
                can_withdraw=account.can_withdraw,
                nonzero_asset_count=account.nonzero_asset_count,
                btc_jpy_tradeable=account.btc_jpy_tradeable,
            )
        ],
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
