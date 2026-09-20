from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.market_data import Candle
from app.trading.application.dummy_signal import generate_dummy_signal


def _candle(close: Decimal, i: int) -> Candle:
    t = datetime(2026, 9, 20, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        source="test",
        is_final=True,
    )


def test_generate_dummy_signal_holds_with_insufficient_candles() -> None:
    candles = [_candle(Decimal("100"), i) for i in range(3)]
    assert generate_dummy_signal(candles, period=5) == "hold"


def test_generate_dummy_signal_buys_when_close_above_average() -> None:
    closes = [Decimal(v) for v in [100, 100, 100, 100, 110]]
    candles = [_candle(c, i) for i, c in enumerate(closes)]
    assert generate_dummy_signal(candles, period=5) == "buy"


def test_generate_dummy_signal_sells_when_close_below_average() -> None:
    closes = [Decimal(v) for v in [100, 100, 100, 100, 90]]
    candles = [_candle(c, i) for i, c in enumerate(closes)]
    assert generate_dummy_signal(candles, period=5) == "sell"


def test_generate_dummy_signal_holds_when_close_equals_average() -> None:
    closes = [Decimal(100) for _ in range(5)]
    candles = [_candle(c, i) for i, c in enumerate(closes)]
    assert generate_dummy_signal(candles, period=5) == "hold"


def test_generate_dummy_signal_uses_only_trailing_window() -> None:
    # Older candles would pull the average down, but only the last 5 matter.
    closes = [Decimal(v) for v in [10, 10, 10, 100, 100, 100, 100, 105]]
    candles = [_candle(c, i) for i, c in enumerate(closes)]
    assert generate_dummy_signal(candles, period=5) == "buy"
