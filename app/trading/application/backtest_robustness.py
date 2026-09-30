"""Risk/robustness measures over a rolling walk-forward's test windows
(2026-09-30, per user request: compare exit policies on "which keeps making
money" and "which survives sudden crashes", with not losing money as the
first priority -- see docs/plans/rolling-walk-forward.md).

**Why windows are chained instead of replaying 9 years in one pass**: the
Risk Gate's consecutive-loss and peak-drawdown locks only clear on a win or a
recovered peak, which a flat account can never produce -- live, a person
releases them. One uninterrupted replay would therefore freeze at the first
lock and compare nothing. The rolling walk-forward already restarts every
30-day window from flat with fresh locks, i.e. it models a monthly review
that releases them; compounding the windows' returns turns that into one
continuous equity curve whose drawdowns can span many months.

Values are plain floats: these are ratios for research reports, not money
that is booked anywhere.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

Curve = Sequence[tuple[datetime, float]]


def chain_windows(windows: Sequence[tuple[Curve, float]]) -> list[tuple[datetime, float]]:
    """`windows` are `(curve, ending)` pairs in time order, each expressed as a
    fraction of that window's own starting equity. Each window is scaled by
    where the previous one ended, so the result is one compounded curve that
    starts at 1.0. Every window's `ending` is appended as its last point
    (at its last curve time), because a window's final fills can move equity
    after its last recorded mark."""
    chained: list[tuple[datetime, float]] = []
    level = 1.0
    for curve, ending in windows:
        chained.extend((time, level * value) for time, value in curve)
        level *= ending
        if curve:
            chained.append((curve[-1][0], level))
    return chained


@dataclass(frozen=True)
class Drawdown:
    depth: float
    """Fraction lost from the peak, e.g. 0.25 for a 25% fall."""
    peak_time: datetime | None
    trough_time: datetime | None


def max_drawdown(curve: Curve) -> Drawdown:
    worst = Drawdown(depth=0.0, peak_time=None, trough_time=None)
    peak_value = float("-inf")
    peak_time: datetime | None = None
    for time, value in curve:
        if value > peak_value:
            peak_value, peak_time = value, time
            continue
        depth = 1 - value / peak_value
        if depth > worst.depth:
            worst = Drawdown(depth=depth, peak_time=peak_time, trough_time=time)
    return worst


def longest_underwater(curve: Curve) -> timedelta:
    """Longest time from a peak until the curve regained it -- or until the
    curve's end, if it never did."""
    longest = timedelta(0)
    peak_value = float("-inf")
    peak_time: datetime | None = None
    for time, value in curve:
        if value >= peak_value:
            if peak_time is not None:
                longest = max(longest, time - peak_time)
            peak_value, peak_time = value, time
    if curve and peak_time is not None and curve[-1][1] < peak_value:
        longest = max(longest, curve[-1][0] - peak_time)
    return longest


def worst_daily_return(curve: Curve) -> float | None:
    """Worst change between consecutive calendar days' last values (UTC
    dates), or None with fewer than two days."""
    closes: dict[object, float] = {}
    for time, value in curve:
        closes[time.date()] = value
    values = list(closes.values())
    if len(values) < 2:
        return None
    return min(
        current / previous - 1 for previous, current in zip(values, values[1:], strict=False)
    )


def window_return(curve: Curve, *, start: datetime, end: datetime) -> float | None:
    """Change from the last value before `start` to the last value at or before
    `end`, or None if the curve has no value on one of those sides."""
    before = [value for time, value in curve if time < start]
    inside = [value for time, value in curve if start <= time <= end]
    if not before or not inside:
        return None
    return inside[-1] / before[-1] - 1


def top_share(amounts: Sequence[float], *, top_n: int) -> float | None:
    """How much of the total the `top_n` largest amounts account for -- near
    1.0 means the result hinges on a few outliers. None when the total is not
    positive (a share of a loss is not meaningful)."""
    total = sum(amounts)
    if total <= 0:
        return None
    return sum(sorted(amounts, reverse=True)[:top_n]) / total
