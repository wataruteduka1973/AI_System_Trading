"""Puts a paper portfolio's results next to what the backtest expects
(docs/plans/paper-trading-live-data.md Unit 6). Used by
`scripts/report_paper_performance.py`; everything here is a pure calculation.

- `live_equity_curve`: an account's equity at every bar close, rebuilt from its
  ledger (cash, fees, deposits) and fills -- cash up to that time plus the quantity
  then held at that bar's close, the same "cash + market value" definition as
  `account_valuation` (spot, long only).
- `combine_curves`: one portfolio from several accounts (a strategy version's bots).
- `summarize`: return and maximum drawdown of a curve, measured the same way for
  live, for the same-period replay and for every historical window, so the three
  are directly comparable.
- `expected_range` / `share_at_or_below` / `range_warnings`: where the live values
  fall among the historical windows, and the two warnings the plan asks for (shown
  only; nothing is stopped automatically).

Ratios are plain floats, as in `backtest_robustness`: they are for reading, not
booked anywhere.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.trading.application import backtest_robustness as rb
from app.trading.application.paper_parity import held_quantity_at

LOW_QUANTILE = 0.05
HIGH_QUANTILE = 0.95

EquityCurve = list[tuple[datetime, Decimal]]


@dataclass(frozen=True)
class CashMovement:
    """A ledger entry that moves cash: `cash`, `fee`, `deposit` or `withdrawal`."""

    occurred_at: datetime
    amount: Decimal


def live_equity_curve(
    closes: Sequence[tuple[datetime, Decimal]],
    cash_movements: Sequence[CashMovement],
    fills: Sequence[tuple[str, Decimal, datetime]],
) -> EquityCurve:
    """`closes` are (close_time, close) of the account's instrument; `fills` are
    (side, quantity, executed_at) as for `held_quantity_at`."""
    curve: EquityCurve = []
    for close_time, close in closes:
        cash = sum(
            (movement.amount for movement in cash_movements if movement.occurred_at <= close_time),
            Decimal(0),
        )
        curve.append((close_time, cash + held_quantity_at(fills, close_time) * close))
    return curve


def combine_curves(curves: Sequence[EquityCurve]) -> EquityCurve:
    """Sums the accounts at the times all of them have a value. A strategy version's
    bots share one timeframe, so their bar closes line up."""
    if not curves:
        return []
    shared = set.intersection(*({time for time, _ in curve} for curve in curves))
    totals: dict[datetime, Decimal] = {}
    for curve in curves:
        for time, value in curve:
            if time in shared:
                totals[time] = totals.get(time, Decimal(0)) + value
    return sorted(totals.items())


@dataclass(frozen=True)
class CurveSummary:
    return_pct: float
    """Last value against `starting_equity`, e.g. 0.012 for +1.2%."""
    max_drawdown_pct: float
    """Largest fall from a peak, e.g. 0.05 for 5%; the start counts as the first peak."""


def summarize(curve: EquityCurve, starting_equity: Decimal) -> CurveSummary:
    start = float(starting_equity)
    if start <= 0:
        raise ValueError("starting equity must be positive")
    points = (
        [(curve[0][0], start), *((time, float(value)) for time, value in curve)] if curve else []
    )
    ending = float(curve[-1][1]) if curve else start
    return CurveSummary(
        return_pct=ending / start - 1,
        max_drawdown_pct=rb.max_drawdown(points).depth,
    )


@dataclass(frozen=True)
class TradeCounts:
    closes: int
    wins: int
    """Closes with positive P&L before fees -- counted the same way for live
    (`realized_pnl` ledger entries) and for the replay (`TradeRecord.realized_pnl`)."""


def count_trades(realized_pnls: Sequence[Decimal]) -> TradeCounts:
    return TradeCounts(closes=len(realized_pnls), wins=sum(1 for pnl in realized_pnls if pnl > 0))


def window_starts(
    first: datetime, last: datetime, *, length: timedelta, stride: timedelta
) -> list[datetime]:
    """Starts of every `length`-long window inside [first, last], `stride` apart."""
    if length <= timedelta(0) or stride <= timedelta(0):
        raise ValueError("length and stride must be positive")
    starts: list[datetime] = []
    start = first
    while start + length <= last:
        starts.append(start)
        start += stride
    return starts


@dataclass(frozen=True)
class ExpectedRange:
    low: float
    median: float
    high: float
    samples: int


def _quantile(ordered: Sequence[float], q: float) -> float:
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def expected_range(samples: Sequence[float]) -> ExpectedRange:
    """The 5% / 50% / 95% points of the historical windows (linear interpolation)."""
    if not samples:
        raise ValueError("no historical windows to compare against")
    ordered = sorted(samples)
    return ExpectedRange(
        low=_quantile(ordered, LOW_QUANTILE),
        median=_quantile(ordered, 0.5),
        high=_quantile(ordered, HIGH_QUANTILE),
        samples=len(ordered),
    )


def share_at_or_below(samples: Sequence[float], value: float) -> float:
    """Share of historical windows whose value is at or below `value`."""
    if not samples:
        raise ValueError("no historical windows to compare against")
    return sum(1 for sample in samples if sample <= value) / len(samples)


def range_warnings(
    live: CurveSummary, returns: ExpectedRange, drawdowns: ExpectedRange
) -> list[str]:
    """The plan's two warnings. They are shown only; no bot is stopped."""
    warnings: list[str] = []
    if live.max_drawdown_pct > drawdowns.high:
        warnings.append(
            f"最大DD {live.max_drawdown_pct:.2%} が想定の95%({drawdowns.high:.2%})を超えています。"
            "停止を検討してください。"
        )
    if live.return_pct < returns.low:
        warnings.append(
            f"損益率 {live.return_pct:+.2%} が想定の5%({returns.low:+.2%})を下回っています。"
        )
    return warnings
