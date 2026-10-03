"""Detached access snapshots; credentials never become progress or audit data."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.exchanges.binance import BinanceSpotTestnetClient
from app.exchanges.oanda import OandaPracticeClient
from app.exchanges.types import CandlePoint
from app.market_data.infrastructure.access import load_credentials, resolve_instrument_access
from app.market_data.infrastructure.leases import FeedKey
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
        access = resolve_instrument_access(db, feed.workspace_id, feed.instrument_id)
        return AccessSnapshot(
            access.exchange.code,
            access.instrument.symbol,
            access.connection.api_base_url,
            access.connection.id,
            access.account.id,
            access.secret_ref,
            access.connection.credentials_updated_at,
            access.selection.updated_at,
        )

    async def fetch(
        self, access: AccessSnapshot, timeframe: str, start: datetime, end: datetime
    ) -> list[CandlePoint]:
        # This method runs without a database transaction or ORM entities.
        credentials = load_credentials(self.secrets, access.secret_ref, access.exchange)
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
