"""`app/trading/application/ema_trend_signal.py`. Crossover fixtures are built
as flat-then-moving price paths and located with `exponential_moving_average`
itself (already unit-tested in test_indicators.py) rather than hand-derived
constants, since a 200+-bar EMA series is not practical to compute by hand."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.market_data.application.indicators import exponential_moving_average
from app.models.market_data import Candle
from app.trading.application.ema_trend_signal import (
    FAST_PERIOD,
    SLOW_PERIOD,
    TREND_PERIOD,
    generate_ema_trend_signal,
)


def _candle(close: Decimal, i: int) -> Candle:
    t = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1h",
        open_time=t,
        close_time=t + timedelta(hours=1),
        open=close,
        high=close,
        low=close,
        close=close,
        source="test",
        is_final=True,
    )


def _ramp(start_price: Decimal, flat_bars: int, steps: list[Decimal]) -> list[Candle]:
    """`flat_bars` candles at `start_price`, then one candle per entry in `steps`
    (each `steps[i]` is that bar's close, not a delta)."""
    candles = [_candle(start_price, i) for i in range(flat_bars)]
    candles += [_candle(price, flat_bars + i) for i, price in enumerate(steps)]
    return candles


def _find_crossover(candles: list[Candle], *, direction: str) -> int | None:
    """First index `i` (into `candles`) where the fast/slow EMA relationship
    flips, evaluated the same way `generate_ema_trend_signal` does (comparing
    `candles[:i+1]` against `candles[:i]`)."""
    for i in range(SLOW_PERIOD, len(candles)):
        window, prev_window = candles[: i + 1], candles[:i]
        fast = exponential_moving_average(window, FAST_PERIOD)
        slow = exponential_moving_average(window, SLOW_PERIOD)
        prev_fast = exponential_moving_average(prev_window, FAST_PERIOD)
        prev_slow = exponential_moving_average(prev_window, SLOW_PERIOD)
        if fast is None or slow is None or prev_fast is None or prev_slow is None:
            continue
        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow
        if (direction == "up" and crossed_up) or (direction == "down" and crossed_down):
            return i
    return None


def test_hold_when_fewer_than_trend_period_plus_one_candles() -> None:
    candles = [_candle(Decimal(100), i) for i in range(TREND_PERIOD)]
    assert generate_ema_trend_signal(candles) == "hold"


def test_buy_on_the_bar_the_fast_ema_crosses_above_the_slow_ema_while_above_trend() -> None:
    # Long flat baseline (>= TREND_PERIOD) keeps EMA(200) anchored near 100, then a
    # steep uniform rally pushes price well above it while EMA(12) pulls ahead of
    # EMA(26).
    candles = _ramp(Decimal(100), flat_bars=220, steps=[Decimal(100 + 5 * i) for i in range(1, 31)])
    crossover_index = _find_crossover(candles, direction="up")
    assert crossover_index is not None, "fixture did not produce a bullish crossover"

    assert generate_ema_trend_signal(candles[: crossover_index + 1]) == "buy"
    assert generate_ema_trend_signal(candles[:crossover_index]) != "buy"


def test_hold_on_a_bullish_crossover_that_stays_below_the_trend_filter() -> None:
    # A long flat baseline anchors EMA(200) near 100; a sharp drop then only a
    # partial recovery produces a bullish fast/slow crossover while price is
    # still well below that anchored trend level.
    drop_steps = [Decimal(100 - 2 * i) for i in range(1, 16)]  # 100 -> 70
    recovery_steps = [Decimal(70) + Decimal("0.6") * i for i in range(1, 41)]  # 70 -> 94, < 100
    candles = _ramp(Decimal(100), flat_bars=400, steps=drop_steps + recovery_steps)
    crossover_index = _find_crossover(candles, direction="up")
    assert crossover_index is not None, "fixture did not produce a bullish crossover"
    assert candles[crossover_index].close < Decimal(100), "fixture must stay below the trend"

    assert generate_ema_trend_signal(candles[: crossover_index + 1]) == "hold"


def test_sell_on_the_bar_the_fast_ema_crosses_below_the_slow_ema_regardless_of_trend() -> None:
    # Same long flat baseline, then a steep decline -- the exit signal must fire
    # even though price is now below the EMA(200) trend filter (only entries are
    # trend-filtered; see the module docstring for why exits never are).
    candles = _ramp(Decimal(100), flat_bars=220, steps=[Decimal(100 - 5 * i) for i in range(1, 31)])
    crossover_index = _find_crossover(candles, direction="down")
    assert crossover_index is not None, "fixture did not produce a bearish crossover"

    assert generate_ema_trend_signal(candles[: crossover_index + 1]) == "sell"


def test_hold_when_fast_and_slow_ema_do_not_cross() -> None:
    candles = [_candle(Decimal(100), i) for i in range(TREND_PERIOD + 5)]
    assert generate_ema_trend_signal(candles) == "hold"
