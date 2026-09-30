"""Minimal technical indicators. Average True Range is the one indicator
`risk_gate.py`'s stop-distance formula needs
(08_取引アルゴリズムとリスク初期値.md§4: `max(ATR(14) * coefficient, spread * 3, ...)`).
`exponential_moving_average` was added 2026-09-28 for `app/trading/application
/ema_trend_signal.py` (comparing candidate hand-crafted strategies against the
dummy SMA5 baseline on real BTCJPY data -- see that module's docstring). No
other indicator infrastructure (rolling windows, caching, a registry of
indicators, etc.) is added; building that out remains out of scope.
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


def exponential_moving_average(candles: Sequence[Candle], period: int) -> Decimal | None:
    """EMA of closes over `period`, seeded by the simple average of the oldest
    `period` closes in `candles`, then rolled forward one bar at a time:
    `EMA[t] = (close[t] - EMA[t-1]) * multiplier + EMA[t-1]`,
    `multiplier = 2 / (period + 1)`.

    Like `average_true_range`, this is stateless: called fresh over whatever
    window `candles` is (the caller does not maintain a running EMA across
    calls), so the result depends on how much history is passed in, not just
    the most recent `period` candles -- with exactly `period` candles there is
    only a seed and no smoothing has happened yet. Returns None if fewer than
    `period` candles are given."""
    if len(candles) < period:
        return None
    closes = [c.close for c in candles]
    multiplier = Decimal(2) / Decimal(period + 1)
    ema = sum(closes[:period], Decimal(0)) / period
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
    return ema
