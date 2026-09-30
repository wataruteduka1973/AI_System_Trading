"""Minimal technical indicators. Average True Range is the one indicator
`risk_gate.py`'s stop-distance formula needs
(08_取引アルゴリズムとリスク初期値.md§4: `max(ATR(14) * coefficient, spread * 3, ...)`).
`exponential_moving_average` was added 2026-09-28 for `app/trading/application
/ema_trend_signal.py` (comparing candidate hand-crafted strategies against the
dummy SMA5 baseline on real BTCJPY data -- see that module's docstring).
`relative_strength_index` was added 2026-09-30 for `app/trading/application
/rsi_mean_reversion_signal.py`, the next candidate in the same comparison. No
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


def relative_strength_index(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    """Wilder's RSI of closes: the average gain and average loss of close-to-close
    changes are seeded by the simple average of the first `period` changes, then
    smoothed the same way as `average_true_range`
    (`avg[t] = (avg[t-1] * (period - 1) + value[t]) / period`), and
    `RSI = 100 - 100 / (1 + avg_gain / avg_loss)`. Stateless like the other
    indicators here, so the result depends on how much history is passed in.

    Edge cases: 100 when there were gains but no losses, 0 when there were losses
    but no gains, and 50 (neutral) when price never changed -- the formula itself
    is undefined there, and treating a flat market as neither overbought nor
    oversold keeps a signal from firing on it. Returns None if fewer than
    `period + 1` candles are given (not enough changes for a seed)."""
    if len(candles) < period + 1:
        return None
    changes = [
        current.close - previous.close
        for previous, current in zip(candles, candles[1:], strict=False)
    ]
    gains = [max(change, Decimal(0)) for change in changes]
    losses = [max(-change, Decimal(0)) for change in changes]
    avg_gain = sum(gains[:period], Decimal(0)) / period
    avg_loss = sum(losses[:period], Decimal(0)) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return Decimal(100) if avg_gain > 0 else Decimal(50)
    return Decimal(100) - Decimal(100) / (1 + avg_gain / avg_loss)
