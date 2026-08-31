from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.exchanges.binance import (
    BinanceApiError,
    BinanceAuthenticationError,
    BinanceInstrumentRules,
    BinanceSpotTestnetClient,
    get_binance_spot_testnet_client,
)
from app.exchanges.oanda import (
    OandaApiError,
    OandaAuthenticationError,
    OandaInstrumentRules,
    OandaPracticeClient,
    get_oanda_practice_client,
)
from app.models.catalog import (
    AuditLog,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    Market,
    Workspace,
    WorkspaceAccountSelection,
)
from app.schemas.catalog import WorkspaceInstrumentRead, WorkspaceInstrumentSyncRead
from app.security.auth import require_owner
from app.services.secrets import LocalEncryptedSecretStore, get_secret_store

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Owner = Annotated[str, Depends(require_owner)]
SecretStore = Annotated[LocalEncryptedSecretStore, Depends(get_secret_store)]
OandaClient = Annotated[OandaPracticeClient, Depends(get_oanda_practice_client)]
BinanceClient = Annotated[BinanceSpotTestnetClient, Depends(get_binance_spot_testnet_client)]


@router.get(
    "/workspaces/{workspace_id}/instruments",
    response_model=list[WorkspaceInstrumentRead],
    tags=["instruments"],
)
def list_workspace_instruments(
    workspace_id: UUID, db: DatabaseSession, _owner: Owner
) -> list[WorkspaceInstrumentRead]:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    rows = db.execute(
        select(Instrument, Exchange, Market)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .join(Market, Instrument.market_id == Market.id)
        .join(
            WorkspaceAccountSelection,
            (WorkspaceAccountSelection.workspace_id == workspace_id)
            & (WorkspaceAccountSelection.exchange_id == Exchange.id),
        )
        .order_by(Exchange.code, Instrument.symbol)
    ).all()
    return [
        _instrument_read(instrument, exchange.code, market.code)
        for instrument, exchange, market in rows
    ]


@router.post(
    "/workspaces/{workspace_id}/instruments/sync",
    response_model=WorkspaceInstrumentSyncRead,
    tags=["instruments"],
)
async def sync_workspace_instruments(
    workspace_id: UUID,
    db: DatabaseSession,
    secret_store: SecretStore,
    oanda_client: OandaClient,
    binance_client: BinanceClient,
    _owner: Owner,
) -> WorkspaceInstrumentSyncRead:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    selections = db.execute(
        select(WorkspaceAccountSelection, ExternalAccount, ExchangeConnection, Exchange)
        .join(ExternalAccount, WorkspaceAccountSelection.external_account_id == ExternalAccount.id)
        .join(ExchangeConnection, ExternalAccount.connection_id == ExchangeConnection.id)
        .join(Exchange, WorkspaceAccountSelection.exchange_id == Exchange.id)
        .where(
            WorkspaceAccountSelection.workspace_id == workspace_id,
            ExchangeConnection.workspace_id == workspace_id,
            ExchangeConnection.exchange_id == Exchange.id,
            ExchangeConnection.status == "verified",
            ExternalAccount.status == "active",
            Exchange.code.in_(("oanda", "binance")),
        )
        .order_by(Exchange.code)
    ).all()
    if not selections:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select an active account from a verified OANDA or Binance connection first",
        )

    loaded_rules: list[tuple[Exchange, OandaInstrumentRules | BinanceInstrumentRules, str]] = []
    for _selection, account, connection, exchange in selections:
        rules, market_code = await _load_rules(
            workspace_id,
            account,
            connection,
            exchange,
            db,
            secret_store,
            oanda_client,
            binance_client,
        )
        loaded_rules.append((exchange, rules, market_code))

    synced: list[WorkspaceInstrumentRead] = []
    for exchange, rules, market_code in loaded_rules:
        market = db.scalar(select(Market).where(Market.code == market_code))
        if market is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Required market catalog entry is missing: {market_code}",
            )
        instrument = _upsert_instrument(db, exchange, market, rules)
        db.flush()
        _add_sync_audit(db, workspace_id, instrument, exchange.code, "succeeded")
        synced.append(_instrument_read(instrument, exchange.code, market.code))
    db.commit()
    return WorkspaceInstrumentSyncRead(instruments=synced)


async def _load_rules(
    workspace_id: UUID,
    account: ExternalAccount,
    connection: ExchangeConnection,
    exchange: Exchange,
    db: Session,
    secret_store: LocalEncryptedSecretStore,
    oanda_client: OandaPracticeClient,
    binance_client: BinanceSpotTestnetClient,
) -> tuple[OandaInstrumentRules | BinanceInstrumentRules, str]:
    if not connection.secret_ref:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selected connection credentials are missing",
        )
    try:
        credentials = secret_store.get(connection.secret_ref)
        if exchange.code == "oanda":
            account_id = secret_store.decrypt_text(account.external_account_ref_encrypted)
            token = credentials["token"]
            rules = await oanda_client.get_instrument_rules(
                connection.api_base_url, token, account_id
            )
            return rules, "foreign_fx_spot"
        api_key = credentials["api_key"]
        secret_key = credentials["secret_key"]
        binance_rules = await binance_client.get_instrument_rules(
            connection.api_base_url, api_key, secret_key
        )
        return binance_rules, "crypto_spot"
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selected connection credentials or account reference cannot be loaded",
        ) from exc
    except (OandaAuthenticationError, BinanceAuthenticationError) as exc:
        _add_failed_sync_audit(
            db, workspace_id, connection.id, exchange.code, "authentication_failed"
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except (OandaApiError, BinanceApiError) as exc:
        _add_failed_sync_audit(
            db, workspace_id, connection.id, exchange.code, "communication_failed"
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _upsert_instrument(
    db: Session,
    exchange: Exchange,
    market: Market,
    rules: OandaInstrumentRules | BinanceInstrumentRules,
) -> Instrument:
    instrument = db.scalar(
        select(Instrument).where(
            Instrument.exchange_id == exchange.id,
            Instrument.market_id == market.id,
            Instrument.symbol == rules.symbol,
        )
    )
    if instrument is None:
        instrument = Instrument(
            exchange_id=exchange.id,
            market_id=market.id,
            symbol=rules.symbol,
        )
        db.add(instrument)
    instrument.base_asset = rules.base_asset
    instrument.quote_asset = rules.quote_asset
    instrument.contract_size = None
    instrument.price_scale = rules.price_scale
    instrument.quantity_scale = rules.quantity_scale
    instrument.tick_size = rules.tick_size
    instrument.step_size = rules.step_size
    instrument.min_quantity = rules.min_quantity
    instrument.max_quantity = rules.max_quantity
    instrument.min_notional = getattr(rules, "min_notional", None)
    instrument.margin_asset = None
    instrument.allowed_order_types = list(getattr(rules, "allowed_order_types", ()))
    instrument.capabilities = (
        {"instrument_type": rules.instrument_type}
        if isinstance(rules, OandaInstrumentRules)
        else {"spot_testnet": True}
    )
    instrument.status = "active"
    instrument.rules_synced_at = datetime.now(UTC)
    instrument.updated_at = datetime.now(UTC)
    return instrument


def _instrument_read(
    instrument: Instrument, exchange_code: str, market_code: str
) -> WorkspaceInstrumentRead:
    return WorkspaceInstrumentRead(
        id=instrument.id,
        exchange_code=exchange_code,
        market_code=market_code,
        symbol=instrument.symbol,
        base_asset=instrument.base_asset,
        quote_asset=instrument.quote_asset,
        price_scale=instrument.price_scale,
        quantity_scale=instrument.quantity_scale,
        tick_size=_decimal_text(instrument.tick_size),
        step_size=_decimal_text(instrument.step_size),
        min_quantity=_optional_decimal_text(instrument.min_quantity),
        max_quantity=_optional_decimal_text(instrument.max_quantity),
        min_notional=_optional_decimal_text(instrument.min_notional),
        allowed_order_types=instrument.allowed_order_types,
        capabilities=instrument.capabilities,
        status=instrument.status,
        rules_synced_at=instrument.rules_synced_at,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _add_sync_audit(
    db: Session,
    workspace_id: UUID,
    instrument: Instrument,
    exchange_code: str,
    outcome: str,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=None,
            action="instrument.rules_synced",
            resource_type="instrument",
            resource_id=instrument.id,
            before_data=None,
            after_data={
                "exchange_code": exchange_code,
                "symbol": instrument.symbol,
                "outcome": outcome,
            },
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )


def _add_failed_sync_audit(
    db: Session,
    workspace_id: UUID,
    connection_id: UUID,
    exchange_code: str,
    outcome: str,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=None,
            action="instrument.rules_sync_failed",
            resource_type="exchange_connection",
            resource_id=connection_id,
            before_data=None,
            after_data={"exchange_code": exchange_code, "outcome": outcome},
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )
