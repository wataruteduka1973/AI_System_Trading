"""Stream ticket issuance use case.

See docs/plans/realtime-market-data-stream.md (work unit 2) and
docs/design/modules/realtime-market-data-stream.md (section 4). This module
only issues the ticket; it never decrypts credentials or opens an exchange
connection (that happens later, when a feed actually starts in work unit 3).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.market_data.application.use_cases import (
    SUPPORTED_TIMEFRAMES,
    MarketDataApplicationError,
)
from app.market_data.infrastructure.leases import FeedKey
from app.market_data.infrastructure.page_access import AccessSnapshot
from app.market_data.infrastructure.stream_tickets import issue_ticket
from app.services.market_data import MarketDataAccessError

ResolveAccess = Callable[[Session, FeedKey], AccessSnapshot]

_ACCESS_ERROR_CODES = {
    "access_unavailable",
    "credentials_missing",
    "credentials_unreadable",
}


@dataclass(frozen=True)
class StreamTicketResult:
    ticket: str
    exchange: str
    symbol: str
    timeframe: str
    expires_at: datetime


def issue_stream_ticket(
    db: Session,
    workspace_id: UUID,
    instrument_id: UUID,
    timeframe: str,
    resolve_access: ResolveAccess,
    ticket_secret: str,
    ttl_seconds: int,
) -> StreamTicketResult:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise MarketDataApplicationError("invalid_input", "Unsupported timeframe")
    feed = FeedKey(workspace_id, instrument_id, timeframe)
    try:
        access = resolve_access(db, feed)
    except MarketDataAccessError as exc:
        code = exc.code if exc.code in _ACCESS_ERROR_CODES else "access_unavailable"
        raise MarketDataApplicationError(code, str(exc)) from exc
    token, expires_at = issue_ticket(
        ticket_secret,
        workspace_id=workspace_id,
        exchange=access.exchange,
        symbol=access.symbol,
        timeframe=timeframe,
        ttl_seconds=ttl_seconds,
    )
    return StreamTicketResult(token, access.exchange, access.symbol, timeframe, expires_at)
