"""Minimal technical indicators. Only Average True Range exists here -- it is the one
indicator `risk_gate.py`'s stop-distance formula needs
(08_取引アルゴリズムとリスク初期値.md§4: `max(ATR(14) * coefficient, spread * 3, ...)`).
No other indicator infrastructure (rolling windows, caching, a registry of indicators,
etc.) is added; building that out is explicitly out of scope for this task.
"""

from collections.abc import Sequence
from decimal import Decimal

from app.models.market_data import Candle


def average_true_range(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    """Simple (unweighted) moving average of the true range over `period` bars, from
    `period + 1` chronologically ascending candles (the extra one supplies the
    "previous close" the first true range needs). This is a plain SMA of true range,
    not Wilder's exponential smoothing that most charting platforms label "ATR" --
    chosen because it needs no persisted smoothing state between calls, which fits
    this module's "minimal, only what's needed" scope. Returns None if fewer than
    `period + 1` candles are given."""
    if len(candles) < period + 1:
        return None
    window = candles[-(period + 1) :]
    true_ranges: list[Decimal] = []
    for previous, current in zip(window, window[1:], strict=False):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(true_ranges, Decimal(0)) / len(true_ranges)
