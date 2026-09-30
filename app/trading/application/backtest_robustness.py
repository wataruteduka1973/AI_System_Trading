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
from datetime import UTC, datetime, timedelta

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


Windows = Sequence[tuple[Curve, float]]
"""`(curve, ending)` per test window, each as fractions of that window's own start."""


def compounded_return(windows: Windows) -> float:
    level = 1.0
    for _, ending in windows:
        level *= ending
    return level - 1


@dataclass(frozen=True)
class WindowSummary:
    total_return: float
    annual_return: float
    first_half_return: float
    second_half_return: float
    last_24_return: float
    top5_share: float | None
    """Share of the total the 5 best windows account for; above 1.0 means the
    rest together lost money."""
    max_drawdown: float
    longest_underwater: timedelta
    worst_day: float | None
    worst_window_return: float
    winning_window_ratio: float


def summarize_windows(windows: Windows, *, years: float) -> WindowSummary:
    """Both questions asked of an exit policy / filter set on 2026-09-30: does it
    keep making money (halves, recent windows, dependence on a few windows), and
    does it survive crashes (drawdown, time underwater, worst day/window)."""
    curve = chain_windows(windows)
    total = compounded_return(windows)
    half = len(windows) // 2
    returns = [ending - 1 for _, ending in windows]
    return WindowSummary(
        total_return=total,
        annual_return=(1 + total) ** (1 / years) - 1,
        first_half_return=compounded_return(windows[:half]),
        second_half_return=compounded_return(windows[half:]),
        last_24_return=compounded_return(windows[-24:]),
        top5_share=top_share(returns, top_n=5),
        max_drawdown=max_drawdown(curve).depth,
        longest_underwater=longest_underwater(curve),
        worst_day=worst_daily_return(curve),
        worst_window_return=min(returns),
        winning_window_ratio=sum(r > 0 for r in returns) / len(returns),
    )


def _utc_day(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


BTC_CRASHES: dict[str, tuple[datetime, datetime]] = {
    name: (_utc_day(start), _utc_day(end) + timedelta(days=1))
    for name, (start, end) in {
        "2018-11 hash war": ("2018-11-14", "2018-12-15"),
        "2020-03 COVID": ("2020-03-08", "2020-03-16"),
        "2021-05 China ban": ("2021-05-12", "2021-05-23"),
        "2022-05 LUNA": ("2022-05-05", "2022-05-18"),
        "2022-06 3AC/Celsius": ("2022-06-10", "2022-06-20"),
        "2022-11 FTX": ("2022-11-06", "2022-11-12"),
        "2024-08 yen carry": ("2024-08-01", "2024-08-07"),
    }.items()
}
"""Historical BTC crashes, fixed before looking at any strategy's result (UTC,
the end day inclusive)."""


def crash_returns(
    curve: Curve, crashes: dict[str, tuple[datetime, datetime]]
) -> dict[str, float | None]:
    return {
        name: window_return(curve, start=start, end=end) for name, (start, end) in crashes.items()
    }


def hold_windows(prices: Sequence[Curve], exposure: float) -> list[tuple[Curve, float]]:
    """Benchmark windows for holding `exposure` of equity in the asset through
    each window, given each window's `(time, close)` series."""
    windows: list[tuple[Curve, float]] = []
    for series in prices:
        start = series[0][1]
        curve = [(time, 1 + exposure * (close / start - 1)) for time, close in series]
        windows.append((curve, curve[-1][1]))
    return windows


def format_summary(label: str, summary: WindowSummary) -> str:
    """One report line for the research scripts."""
    top5 = "-" if summary.top5_share is None else f"{summary.top5_share:.0%}"
    worst_day = "-" if summary.worst_day is None else f"{summary.worst_day:+.2%}"
    return (
        f"{label:<34} cagr={summary.annual_return:>+6.2%} total={summary.total_return:>+7.1%} "
        f"1st-half={summary.first_half_return:>+7.1%} "
        f"2nd-half={summary.second_half_return:>+7.1%} "
        f"last24={summary.last_24_return:>+6.1%} top5-share={top5:>5} | "
        f"maxDD={summary.max_drawdown:>5.1%} "
        f"underwater={summary.longest_underwater.days:>4}d worst-day={worst_day:>7} "
        f"worst-window={summary.worst_window_return:>+6.2%} "
        f"win-windows={summary.winning_window_ratio:>4.0%}"
    )
