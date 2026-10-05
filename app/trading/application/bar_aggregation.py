"""Builds coarser research bars (2h, 6h, 8h, 12h) from stored 1h bars
(docs/plans/decision-timeframes.md, 2026-10-04).

**Why build them instead of storing them**: the candle table and the market-data
feeds only accept 1m/5m/15m/30m/1h/4h/1d (a DB check constraint, and
`TIMEFRAME_SECONDS` doubles as the stream's accepted-timeframe list). Adding
timeframes there would change the market-data subsystem for a research
question; combining 1h bars in memory answers it without touching either.

**Same bars as Binance's**: Binance's 2h-12h klines start at UTC multiples of
their length, so each bucket here does too, regardless of where the series
starts. A bucket with any 1h bar missing (exchange maintenance) is dropped --
a bar built from part of its period would have a wrong high/low.

The bars are transient `Candle` objects (never added to a session).
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.market_data import Candle


def aggregate_bars(hourly: Sequence[Candle], *, hours_per_bar: int) -> list[Candle]:
    """`hourly` ascending; returns complete `hours_per_bar`-hour bars, ascending."""
    if hours_per_bar <= 0 or 24 % hours_per_bar != 0:
        raise ValueError("hours_per_bar must divide a day (1, 2, 3, 4, 6, 8, 12, 24)")
    bar_seconds = hours_per_bar * 3600
    buckets: dict[int, list[Candle]] = {}
    for candle in hourly:
        bucket = int(candle.open_time.timestamp()) // bar_seconds * bar_seconds
        buckets.setdefault(bucket, []).append(candle)

    bars: list[Candle] = []
    for bucket, members in sorted(buckets.items()):
        if len(members) != hours_per_bar:
            continue
        open_time = datetime.fromtimestamp(bucket, UTC)
        bars.append(
            Candle(
                id=uuid4(),
                instrument_id=members[0].instrument_id,
                timeframe=f"{hours_per_bar}h",
                open_time=open_time,
                close_time=open_time + timedelta(seconds=bar_seconds),
                open=members[0].open,
                high=max(m.high for m in members),
                low=min(m.low for m in members),
                close=members[-1].close,
                volume=sum((m.volume or Decimal(0) for m in members), Decimal(0)),
                source="aggregated-1h",
                is_final=True,
            )
        )
    return bars
