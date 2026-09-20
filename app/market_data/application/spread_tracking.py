"""Persists the latest observed bid/ask per instrument (`fx.instrument_spread`), fed
from the live OANDA PricingStream connection. See `InstrumentSpread`'s docstring
(app/models/market_data.py) and `OandaFeedWorker.record_spread`'s docstring
(app/market_data/infrastructure/oanda_stream.py) for the full "why".
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.market_data import InstrumentSpread


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
    design shouldn't produce one."""
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
