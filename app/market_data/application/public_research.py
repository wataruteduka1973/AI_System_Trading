"""Provisioning and backfill for the "binance_public" research-only
`Instrument` (2026-09-26, per user decision -- see
`app/exchanges/binance_public.py`'s module docstring for why this exists:
Binance Spot Testnet's own BTCJPY history is too illiquid to validate a
strategy against, so real production klines are fetched read-only instead,
under their own Instrument so they never mix with the Testnet price series
actually used for paper execution).

Kept separate from `use_cases.py`: every function there assumes a credentialed
`ExchangeConnection`/`WorkspaceAccountSelection` exists for the instrument in
question, which a public-data research instrument deliberately has none of.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exchanges.binance_public import BinancePublicClient
from app.exchanges.types import timeframe_delta
from app.market_data.infrastructure.candle_store import upsert_candle_points
from app.models.connections import Exchange, Market
from app.models.instruments import Instrument
from app.models.market_data import Candle

RESEARCH_EXCHANGE_CODE = "binance_public"
_PAGE_SIZE_CANDLES = 1000  # matches Binance's own per-request cap (MAX_KLINES_PER_REQUEST)


class PublicResearchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def find_public_research_instrument(db: Session, symbol: str) -> Instrument | None:
    """The already-fetched research instrument for `symbol`, or None if
    `scripts/fetch_binance_public_history.py` has not been run for it yet."""
    return db.scalar(
        select(Instrument)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
        .where(Exchange.code == RESEARCH_EXCHANGE_CODE, Instrument.symbol == symbol)
    )


async def ensure_public_research_instrument(
    db: Session, client: BinancePublicClient, symbol: str = "BTCJPY"
) -> Instrument:
    """Idempotent: creates the instrument on first call, refreshes its trading
    rules (tick/step size, notional floor) from Binance's real `exchangeInfo`
    every call after that. Mirrors `app/api/routes/instruments.py`'s
    `_upsert_instrument`, minus the connection/credentials it has no need for."""
    exchange = db.scalar(select(Exchange).where(Exchange.code == RESEARCH_EXCHANGE_CODE))
    if exchange is None:
        raise PublicResearchError(
            "exchange_not_seeded",
            "The binance_public exchange catalog entry is missing "
            "(run migrations -- see alembic/versions/20260926_0009_*)",
        )
    market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
    if market is None:
        raise PublicResearchError(
            "market_not_seeded", "The crypto_spot market catalog entry is missing"
        )

    rules = await client.get_instrument_rules(symbol)
    instrument = db.scalar(
        select(Instrument).where(
            Instrument.exchange_id == exchange.id,
            Instrument.market_id == market.id,
            Instrument.symbol == symbol,
        )
    )
    if instrument is None:
        instrument = Instrument(exchange_id=exchange.id, market_id=market.id, symbol=symbol)
        db.add(instrument)
    instrument.base_asset = rules.base_asset
    instrument.quote_asset = rules.quote_asset
    instrument.price_scale = rules.price_scale
    instrument.quantity_scale = rules.quantity_scale
    instrument.tick_size = rules.tick_size
    instrument.step_size = rules.step_size
    instrument.min_quantity = rules.min_quantity
    instrument.max_quantity = rules.max_quantity
    instrument.min_notional = rules.min_notional
    instrument.allowed_order_types = list(rules.allowed_order_types)
    instrument.capabilities = {"research_only": True, "source": RESEARCH_EXCHANGE_CODE}
    instrument.status = "active"
    instrument.rules_synced_at = datetime.now(UTC)
    instrument.updated_at = datetime.now(UTC)
    db.flush()
    return instrument


async def _fetch_and_upsert(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    *,
    start: datetime,
    end: datetime,
    quality_status: str,
) -> tuple[int, int]:
    """Fetch `[start, end)` in `_PAGE_SIZE_CANDLES`-sized pages and upsert + commit
    each page's final candles as it arrives. Returns (inserted, updated)."""
    delta = timeframe_delta(timeframe)
    cursor = start
    total_inserted = 0
    total_updated = 0
    while cursor < end:
        page_end = min(end, cursor + delta * _PAGE_SIZE_CANDLES)
        points = await client.get_candles(instrument.symbol, timeframe, cursor, page_end)
        final_points = [point for point in points if point.is_final and point.close_time <= end]
        if final_points:
            inserted, updated = upsert_candle_points(
                db, instrument.id, timeframe, RESEARCH_EXCHANGE_CODE, quality_status, final_points
            )
            total_inserted += inserted
            total_updated += updated
            db.commit()
        cursor = page_end
    return total_inserted, total_updated


async def backfill_public_klines(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    days: int,
) -> tuple[int, int]:
    """Fetches `[now - days, now)` page by page, committing each page rather than
    holding one huge uncommitted transaction for a full year of 1m data (~526
    pages). Returns (total_inserted, total_updated). Caller is responsible for
    pacing between calls (see `scripts/fetch_binance_public_history.py`) -- this
    function makes no attempt at its own rate limiting."""
    end = datetime.now(UTC)
    return await _fetch_and_upsert(
        db,
        client,
        instrument,
        timeframe,
        start=end - timedelta(days=days),
        end=end,
        quality_status="backfilled",
    )


async def refresh_public_klines(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    *,
    initial_bars: int,
    now: datetime,
) -> int:
    """Keep a paper-trading bot's public price series current
    (docs/plans/paper-trading-live-data.md Unit 1). Returns the number of new
    candles stored.

    Calls Binance only once the bar after the latest stored one should have
    closed -- the trading worker polls every few seconds, and a 4h series needs
    one request per bar, not one per poll. Fetching restarts at the latest
    stored bar, so a gap left while the worker was stopped is filled on the
    next refresh. With no stored bars it fetches the last `initial_bars` bars,
    enough history for the strategy's lookback. Stored as `complete`: these
    are ordinary closed bars, not a historical backfill."""
    delta = timeframe_delta(timeframe)
    latest = db.scalar(
        select(Candle)
        .where(
            Candle.instrument_id == instrument.id,
            Candle.timeframe == timeframe,
            Candle.is_final.is_(True),
        )
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if latest is not None and latest.close_time + delta > now:
        return 0
    start = latest.open_time if latest is not None else now - delta * initial_bars
    inserted, _ = await _fetch_and_upsert(
        db, client, instrument, timeframe, start=start, end=now, quality_status="complete"
    )
    return inserted
