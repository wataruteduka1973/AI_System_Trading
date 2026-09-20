"""**Placeholder signal logic, originally for pipeline wiring only -- not a real
trading strategy.** A real predictive model (e.g. Chronos) is deliberately not
integrated yet (separate, on-hold task); this exists so the Signal -> RiskDecision ->
OrderIntent -> TradeOrder -> Fill pipeline can be exercised end-to-end while that
model integration is pending. Do not tune this to chase performance -- it is
intentionally a simple, explainable rule, not a strategy under active development.

**Update (Horizon4-lite, 2026-09-20, per user confirmation -- see
`docs/decisions/0003-horizon4-lite-backtest-before-chronos.md` and
`docs/plans/horizon4-lite-backtest.md`)**: this function is now also the default
`signal_generator` for `backtest_replay.run_replay`, serving as the non-AI baseline
Chronos will eventually be compared against. The original "do not backtest this"
line above no longer applies -- it described a time before any backtest capability
existed to point at; comparing this rule's backtest performance against Chronos's is
now the explicit purpose ADR 0003 exists for.
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
