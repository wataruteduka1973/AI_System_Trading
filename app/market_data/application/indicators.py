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
    """Wilder's exponential smoothing of the true range (2026-09-20; the original
    version was a plain SMA of true range -- see git history), over `period` bars, from
    chronologically ascending candles. True Range itself is unchanged:
    `max(high-low, |high-previous_close|, |low-previous_close|)`.

    Wilder's formula is recursive: the first ATR is the simple average of the first
    `period` true ranges (the "seed"), and each subsequent one blends the prior ATR
    with the new true range: `ATR[t] = (ATR[t-1] * (period - 1) + TR[t]) / period`.
    This means the result **depends on how much history is passed in**, not just on
    the most recent `period + 1` candles: with exactly `period + 1` candles there is
    only a seed and no smoothing has happened yet (identical to the old SMA version);
    with more history, each additional candle rolls the smoothing forward and more
    heavily weights recent volatility, which is the whole point of using Wilder's
    method here (see the task this was implemented for: faster reaction to recent
    volatility than a flat SMA, since both crypto and FX can move sharply).
    `risk_gate.py`'s caller was updated to fetch more than the bare minimum candles
    for this reason. Returns None if fewer than `period + 1` candles are given (not
    enough for even a seed)."""
    if len(candles) < period + 1:
        return None
    true_ranges: list[Decimal] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    atr = sum(true_ranges[:period], Decimal(0)) / period
    for true_range in true_ranges[period:]:
        atr = (atr * (period - 1) + true_range) / period
    return atr
