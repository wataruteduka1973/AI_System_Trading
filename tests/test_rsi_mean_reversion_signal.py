"""`app/trading/application/rsi_mean_reversion_signal.py`. Price paths are
steady +1 ramps (so Wilder's average gain is exactly 1 and average loss 0)
followed by hand-sized jumps, which keeps each RSI(2) value hand-computable:
after the ramp, a -10 bar gives gain 0.5 / loss 5 -> RSI 9.09 (< 10); two +10
bars after that give RSI 67.7 then 85.9 (> 70)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.market_data import Candle
from app.trading.application.rsi_mean_reversion_signal import (
    generate_rsi_mean_reversion_signal,
)

_RAMP = list(range(100, 200))  # 100 bars rising +1; ends at 199


def _candles(closes: list[int]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for i, close in enumerate(closes):
        t = start + timedelta(hours=i)
        price = Decimal(close)
        candles.append(
            Candle(
                id=uuid4(),
                instrument_id=uuid4(),
                timeframe="1h",
                open_time=t,
                close_time=t + timedelta(hours=1),
                open=price,
                high=price,
                low=price,
                close=price,
                source="test",
                is_final=True,
            )
        )
    return candles


def _signal_at_end(closes: list[int], *, trend_period: int = 50) -> str:
    return generate_rsi_mean_reversion_signal(
        _candles(closes), rsi_period=2, oversold=Decimal(10), exit_level=Decimal(70),
        trend_period=trend_period,
    )  # fmt: skip


def test_holds_without_enough_history() -> None:
    assert _signal_at_end([100, 90, 80]) == "hold"


def test_buys_on_the_bar_rsi_first_drops_below_oversold_in_an_uptrend() -> None:
    # 189 is above EMA(50) of the ramp (~175).
    assert _signal_at_end([*_RAMP, 189]) == "buy"


def test_does_not_buy_the_dip_when_the_close_is_below_the_trend_filter() -> None:
    # Same dip, but EMA(5) hugs the ramp (~197), so 189 is below the trend.
    assert _signal_at_end([*_RAMP, 189], trend_period=5) == "hold"


def test_does_not_buy_again_while_rsi_stays_oversold() -> None:
    assert _signal_at_end([*_RAMP, 189, 179]) == "hold"


def test_sells_on_the_bar_rsi_first_rises_above_the_exit_level() -> None:
    assert _signal_at_end([*_RAMP, 189, 199]) == "hold"  # RSI 67.7
    assert _signal_at_end([*_RAMP, 189, 199, 209]) == "sell"  # RSI 85.9


def test_the_exit_is_not_trend_filtered() -> None:
    # A falling ramp then a +3 bounce: RSI(2) jumps from 0 to 75 while the close
    # is still far below EMA(50) -- the exit must fire anyway.
    falling = list(range(199, 99, -1))  # ends at 100
    assert _signal_at_end([*falling, 103]) == "sell"
