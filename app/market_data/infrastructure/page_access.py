"""Detached access snapshots; credentials never become progress or audit data."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exchanges.binance import BinanceSpotTestnetClient
from app.exchanges.oanda import OandaPracticeClient
from app.exchanges.types import CandlePoint
from app.market_data.infrastructure.leases import FeedKey
from app.models.catalog import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    Workspace,
    WorkspaceAccountSelection,
)
from app.services.market_data import MarketDataAccessError
from app.services.secrets import LocalEncryptedSecretStore


@dataclass(frozen=True)
class AccessSnapshot:
    exchange: str
    symbol: str
    base_url: str
    connection_id: UUID
    account_id: UUID
    secret_ref: str = field(repr=False)
    credentials_updated_at: datetime | None
    selection_updated_at: datetime


class PageAccess:
    def __init__(
        self,
        secrets: LocalEncryptedSecretStore,
        *,
        oanda: OandaPracticeClient | None = None,
        binance: BinanceSpotTestnetClient | None = None,
    ) -> None:
        self.secrets = secrets
        self.oanda = oanda or OandaPracticeClient()
        self.binance = binance or BinanceSpotTestnetClient()

    def resolve(self, db: Session, feed: FeedKey) -> AccessSnapshot:
        # Shared locks hold the authorization decision stable through commit.
        row = db.execute(
            select(
                Instrument, Exchange, ExchangeConnection, ExternalAccount, WorkspaceAccountSelection
            )
            .join(Exchange, Instrument.exchange_id == Exchange.id)
            .join(
                WorkspaceAccountSelection,
                (WorkspaceAccountSelection.workspace_id == feed.workspace_id)
                & (WorkspaceAccountSelection.exchange_id == Exchange.id),
            )
            .join(Workspace, Workspace.id == WorkspaceAccountSelection.workspace_id)
            .join(
                ExternalAccount, ExternalAccount.id == WorkspaceAccountSelection.external_account_id
            )
            .join(ExchangeConnection, ExchangeConnection.id == ExternalAccount.connection_id)
            .where(
                Instrument.id == feed.instrument_id,
                Instrument.status == "active",
                Exchange.status == "active",
                Workspace.status == "active",
                ExternalAccount.status == "active",
                ExchangeConnection.workspace_id == feed.workspace_id,
                ExchangeConnection.exchange_id == Exchange.id,
                ExchangeConnection.status == "verified",
                ExternalAccount.environment == ExchangeConnection.environment,
            )
            .with_for_update(read=True)
        ).one_or_none()
        if row is None:
            raise MarketDataAccessError("Market-data access unavailable", "access_unavailable")
        instrument, exchange, connection, account, selection = row
        if exchange.code == "binance" and connection.environment == "testnet":
            BinanceSpotTestnetClient._validate_testnet_url(connection.api_base_url)
        elif exchange.code == "oanda" and connection.environment == "practice":
            OandaPracticeClient._validate_practice_url(connection.api_base_url)
        else:
            raise MarketDataAccessError("Unsupported environment", "access_unavailable")
        if not connection.secret_ref:
            raise MarketDataAccessError("Credentials missing", "credentials_missing")
        return AccessSnapshot(
            exchange.code,
            instrument.symbol,
            connection.api_base_url,
            connection.id,
            account.id,
            connection.secret_ref,
            connection.credentials_updated_at,
            selection.updated_at,
        )

    async def fetch(
        self, access: AccessSnapshot, timeframe: str, start: datetime, end: datetime
    ) -> list[CandlePoint]:
        # This method runs without a database transaction or ORM entities.
        try:
            credentials = self.secrets.get(access.secret_ref)
        except (KeyError, ValueError, OSError) as exc:
            raise MarketDataAccessError("Credentials unreadable", "credentials_unreadable") from exc
        required = ("token",) if access.exchange == "oanda" else ("api_key", "secret_key")
        if not all(credentials.get(key) for key in required):
            raise MarketDataAccessError("Credentials missing", "credentials_missing")
        if access.exchange == "oanda":
            return await self.oanda.get_candles(
                access.base_url, credentials["token"], access.symbol, timeframe, start, end
            )
        return await self.binance.get_candles(
            access.base_url,
            credentials["api_key"],
            credentials["secret_key"],
            access.symbol,
            timeframe,
            start,
            end,
        )
