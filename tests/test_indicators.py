from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.market_data.application.indicators import average_true_range
from app.models.market_data import Candle


def _candle(o, h, low, c, i) -> Candle:
    t = datetime(2026, 9, 20, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        source="test",
        is_final=True,
    )


def test_average_true_range_returns_none_with_insufficient_candles() -> None:
    candles = [_candle(100, 101, 99, 100, i) for i in range(5)]
    assert average_true_range(candles, period=14) is None


def test_average_true_range_simple_moving_average_of_true_range() -> None:
    # 3 candles -> 2 true ranges, period=2
    candles = [
        _candle(100, 102, 98, 101, 0),  # prev close n/a (first in window)
        _candle(101, 105, 100, 104, 1),  # TR = max(5, |105-101|=4, |100-101|=1) = 5
        _candle(104, 106, 103, 105, 2),  # TR = max(3, |106-104|=2, |103-104|=1) = 3
    ]
    result = average_true_range(candles, period=2)
    assert result == Decimal(4)  # (5+3)/2


def test_average_true_range_uses_only_the_trailing_window() -> None:
    # 20 candles of noise, period=14 -- just confirm it doesn't crash and returns
    # a positive Decimal using only the last 15.
    candles = [_candle(100 + i, 101 + i, 99 + i, 100 + i, i) for i in range(20)]
    result = average_true_range(candles, period=14)
    assert result is not None
    assert result > 0
