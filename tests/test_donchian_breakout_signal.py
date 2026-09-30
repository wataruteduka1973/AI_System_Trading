"""`app/trading/application/donchian_breakout_signal.py`. Candles are flat bars
(open == high == low == close) so each bar's channel is just its closes, which
keeps the expected breakout bars easy to verify by hand."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.market_data import Candle
from app.trading.application.donchian_breakout_signal import generate_donchian_breakout_signal


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


def _signals(closes: list[int]) -> list[str]:
    """The signal at every bar, each computed only from bars up to and including it."""
    candles = _candles(closes)
    return [
        generate_donchian_breakout_signal(candles[: i + 1], entry_period=3, exit_period=2)
        for i in range(len(candles))
    ]


def test_holds_until_there_is_enough_history_for_both_channels() -> None:
    # entry_period=3 needs 3 prior bars plus 1 more to know the previous bar
    # was not already a breakout: the first 4 bars can never signal.
    assert _signals([100, 100, 100, 200]) == ["hold"] * 4


def test_buys_only_on_the_first_bar_that_closes_above_the_entry_channel() -> None:
    signals = _signals([100, 100, 100, 100, 101, 102, 103])
    assert signals[4] == "buy"  # 101 > max(100, 100, 100)
    assert signals[5:] == ["hold", "hold"]  # still breaking out, but not a new breakout


def test_a_close_equal_to_the_channel_high_is_not_a_breakout() -> None:
    assert _signals([100, 100, 100, 100, 100])[4] == "hold"


def test_buys_again_after_a_pause_resets_the_breakout() -> None:
    signals = _signals([100, 100, 100, 100, 101, 101, 102])
    assert signals[4] == "buy"
    assert signals[5] == "hold"  # 101 is not above max(100, 100, 101)
    assert signals[6] == "buy"  # 102 > max(100, 101, 101), after a non-breakout bar


def test_sells_only_on_the_first_bar_that_closes_below_the_exit_channel() -> None:
    signals = _signals([100, 100, 100, 100, 110, 108, 105, 104])
    assert signals[5] == "hold"  # 108 is not below min(100, 110)
    assert signals[6] == "sell"  # 105 < min(110, 108)
    assert signals[7] == "hold"  # 104 < min(108, 105), but not a new breakdown


def test_default_periods_are_the_classic_20_and_10() -> None:
    closes = [100] * 21 + [101]
    assert generate_donchian_breakout_signal(_candles(closes)) == "buy"
    assert generate_donchian_breakout_signal(_candles(closes[1:])) == "hold"  # 20 prior bars only
