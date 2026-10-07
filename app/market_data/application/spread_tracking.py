"""Persists the latest observed bid/ask per instrument (`fx.instrument_spread`) and a sampled
history of the observations (`fx.instrument_spread_history`). Fed from the live OANDA
PricingStream connection and, for Binance, from the public book ticker
(`app/trading/application/public_price_refresh.py`). See `InstrumentSpread`'s docstring
(app/models/market_data.py) and `OandaFeedWorker.record_spread`'s docstring
(app/market_data/infrastructure/oanda_stream.py) for the full "why".
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.market_data import InstrumentSpread

logger = logging.getLogger(__name__)

HISTORY_MIN_INTERVAL = timedelta(minutes=5)
"""At most one history row per instrument per this interval (decided 2026-10-07; it was 30
seconds when the table was added). OANDA's stream ticks several times a second, Binance's
book ticker is read every 30 s; the history only has to be fine enough to price a bar's
fill, and the finest bars the strategies trade are 1 hour. At 5 minutes a symbol adds about
290 rows a day (~60 MB a year for six symbols), so nothing needs pruning for years."""

_INSERT_HISTORY = text(
    """
    INSERT INTO fx.instrument_spread_history (instrument_id, observed_at, bid, ask, source)
    SELECT :instrument_id, :observed_at, :bid, :ask, :source
    WHERE NOT EXISTS (
      SELECT 1 FROM fx.instrument_spread_history
      WHERE instrument_id = :instrument_id
        AND observed_at > CAST(:observed_at AS timestamptz) - make_interval(secs => :seconds)
    )
    ON CONFLICT DO NOTHING
    """
)


def record_spread_observation(
    db: Session,
    instrument_id: UUID,
    *,
    bid: Decimal,
    ask: Decimal,
    observed_at: datetime,
    source: str,
) -> None:
    """UPSERT the latest bid/ask for one instrument; commits its own transaction. Called
    once per tick from OandaFeedWorker's background thread, independent of any
    request-scoped session. The `where` guard skips the update (leaving the existing row
    untouched) if `observed_at` is not newer than what's already stored, protecting
    against an out-of-order tick even though today's single-writer-per-instrument
    design shouldn't produce one.

    Then appends the observation to the history unless one was already stored in the last
    `HISTORY_MIN_INTERVAL`. That second step is separate and best effort: the latest value
    is what fills and the Risk Gate read, so a history failure (the table is missing until
    `alembic upgrade head` has run, say) is logged and must not take the latest value down."""
    statement = pg_insert(InstrumentSpread).values(
        instrument_id=instrument_id,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        source=source,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[InstrumentSpread.instrument_id],
        set_={
            "bid": statement.excluded.bid,
            "ask": statement.excluded.ask,
            "observed_at": statement.excluded.observed_at,
            "source": statement.excluded.source,
            "updated_at": datetime.now(UTC),
        },
        where=(InstrumentSpread.observed_at < statement.excluded.observed_at),
    )
    db.execute(statement)
    db.commit()
    try:
        db.execute(
            _INSERT_HISTORY,
            {
                "instrument_id": instrument_id,
                "observed_at": observed_at,
                "bid": bid,
                "ask": ask,
                "source": source,
                "seconds": HISTORY_MIN_INTERVAL.total_seconds(),
            },
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("spread history not stored (%s)", type(exc).__name__)
