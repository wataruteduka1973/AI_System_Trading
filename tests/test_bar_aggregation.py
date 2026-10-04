"""`app/trading/application/bar_aggregation.py`: research bars (2h/6h/8h/12h) built
from stored 1h bars, aligned to UTC multiples the way Binance aligns them."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models.market_data import Candle
from app.trading.application.bar_aggregation import aggregate_bars


def _hour(start: datetime, i: int, o: int, h: int, low: int, c: int, v: int = 1) -> Candle:
    t = start + timedelta(hours=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1h",
        open_time=t,
        close_time=t + timedelta(hours=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
        source="test",
        is_final=True,
    )


START = datetime(2026, 1, 1, tzinfo=UTC)


def test_bars_combine_open_high_low_close_and_volume_within_each_utc_bucket() -> None:
    hours = [
        _hour(START, 0, 100, 105, 99, 104, 2),
        _hour(START, 1, 104, 110, 103, 108, 3),
        _hour(START, 2, 108, 109, 95, 96, 4),
        _hour(START, 3, 96, 100, 94, 99, 5),
    ]

    [first, second] = aggregate_bars(hours, hours_per_bar=2)

    assert (first.open_time, first.close_time) == (START, START + timedelta(hours=2))
    assert (first.open, first.high, first.low, first.close) == (100, 110, 99, 108)
    assert first.volume == 5
    assert (second.open, second.high, second.low, second.close) == (108, 109, 94, 99)
    assert first.is_final and second.is_final


def test_a_bucket_missing_an_hour_is_dropped_rather_than_built_incomplete() -> None:
    # Hour 1 missing (e.g. exchange maintenance): the 00:00-02:00 bar cannot be built.
    hours = [_hour(START, i, 100, 101, 99, 100) for i in (0, 2, 3)]
    bars = aggregate_bars(hours, hours_per_bar=2)
    assert [b.open_time for b in bars] == [START + timedelta(hours=2)]


def test_buckets_are_aligned_to_utc_multiples_not_to_the_first_bar() -> None:
    # The series starts at 01:00: with 2h bars, 00:00-02:00 is incomplete and dropped,
    # and the first bar is 02:00-04:00 -- as on Binance, not 01:00-03:00.
    hours = [_hour(START, i, 100, 101, 99, 100) for i in range(1, 5)]
    bars = aggregate_bars(hours, hours_per_bar=2)
    assert [b.open_time for b in bars] == [START + timedelta(hours=2)]


@pytest.mark.parametrize("hours_per_bar", [0, 5, 7, 25])
def test_only_divisors_of_a_day_are_accepted(hours_per_bar: int) -> None:
    with pytest.raises(ValueError):
        aggregate_bars([], hours_per_bar=hours_per_bar)
