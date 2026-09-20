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


def test_average_true_range_seed_is_simple_average_with_exactly_period_plus_one_candles() -> None:
    # period + 1 candles -> exactly `period` true ranges -> only a seed, no Wilder
    # smoothing step has happened yet (matches the pre-Wilder SMA behavior exactly).
    candles = [
        _candle(100, 102, 98, 101, 0),  # prev close n/a (first in window)
        _candle(101, 105, 100, 104, 1),  # TR = max(5, |105-101|=4, |100-101|=1) = 5
        _candle(104, 106, 103, 105, 2),  # TR = max(3, |106-104|=2, |103-104|=1) = 3
    ]
    result = average_true_range(candles, period=2)
    assert result == Decimal(4)  # (5+3)/2


def test_average_true_range_wilder_smoothing_decays_an_old_spike() -> None:
    """Demonstrates the actual behavior change from the old SMA version: an old true
    range spike's influence shrinks as more recent, calmer bars are rolled forward
    through Wilder's formula -- this is the "faster reaction to recent volatility"
    property the task asked for. Candles are built so true ranges are exactly
    [20, 2, 2, 2, ...] (an old spike of 20, then steady calm bars of 2); see each
    candle's high/low/close below for how each TR value is produced."""
    baseline_close = Decimal(100)
    c0 = _candle(100, 100, 100, baseline_close, 0)  # only supplies "previous close"
    c_spike = _candle(100, 110, 90, baseline_close, 1)  # TR = max(20, 10, 10) = 20
    calm = [_candle(100, 101, 99, baseline_close, i) for i in range(2, 6)]  # TR = 2 each

    # Seed only (period+1 candles): dominated by the spike.
    seed = average_true_range([c0, c_spike, calm[0]], period=2)
    assert seed == Decimal(11)  # (20 + 2) / 2

    # One smoothing step further (one more calm candle rolled in).
    one_step = average_true_range([c0, c_spike, calm[0], calm[1]], period=2)
    assert one_step == Decimal("6.5")  # (11*1 + 2) / 2

    # Two smoothing steps further: the spike's influence keeps shrinking.
    two_steps = average_true_range([c0, c_spike, calm[0], calm[1], calm[2]], period=2)
    assert two_steps == Decimal("4.25")  # (6.5*1 + 2) / 2

    assert seed > one_step > two_steps  # monotonically decaying toward the calm TR (2)


def test_average_true_range_matches_wilder_formula_over_a_longer_series() -> None:
    candles = [_candle(100 + i, 101 + i, 99 + i, 100 + i, i) for i in range(20)]
    true_ranges = [Decimal(2)] * 19  # this series has a constant TR of 2 every bar
    period = 14
    expected = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        expected = (expected * (period - 1) + tr) / period
    assert average_true_range(candles, period=period) == expected
