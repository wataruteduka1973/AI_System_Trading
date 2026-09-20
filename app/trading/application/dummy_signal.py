"""**Placeholder signal logic for pipeline wiring only -- not a real trading
strategy.** A real predictive model (e.g. Chronos) is deliberately not integrated yet
(separate, on-hold task); this exists solely so the Signal -> RiskDecision ->
OrderIntent -> TradeOrder -> Fill pipeline can be exercised end-to-end while that
model integration is pending. Do not tune, backtest, or treat this as a strategy
worth evaluating -- it is scaffolding.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from app.models.market_data import Candle

DummySignalAction = Literal["buy", "sell", "hold"]


def generate_dummy_signal(candles: Sequence[Candle], period: int = 5) -> DummySignalAction:
    """Compares the latest final candle's close to the simple moving average of the
    last `period` closes: close above the average -> "buy", below -> "sell", equal or
    not enough candles yet -> "hold"."""
    if len(candles) < period:
        return "hold"
    window = candles[-period:]
    average = sum((c.close for c in window), Decimal(0)) / period
    latest_close = window[-1].close
    if latest_close > average:
        return "buy"
    if latest_close < average:
        return "sell"
    return "hold"
