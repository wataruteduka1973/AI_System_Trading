"""`app/trading/application/paper_performance.py` (docs/plans/paper-trading-live-data.md
Unit 6): live equity rebuilt from the ledger and fills, portfolios, and where the live
values fall among the historical windows."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.trading.application import paper_performance as pp

T0 = datetime(2026, 10, 2, 16, tzinfo=UTC)
H4 = timedelta(hours=4)


def _closes(*prices: str) -> list[tuple[datetime, Decimal]]:
    return [(T0 + H4 * i, Decimal(price)) for i, price in enumerate(prices)]


def test_equity_is_cash_so_far_plus_the_held_quantity_at_each_close() -> None:
    deposit = pp.CashMovement(T0 - timedelta(days=1), Decimal(1000))
    # Bought 2 @ 100 (fee 0.2) at the second bar's close, sold 2 @ 120 (fee 0.24) at the fourth.
    buy = [pp.CashMovement(T0 + H4, Decimal(-200)), pp.CashMovement(T0 + H4, Decimal("-0.2"))]
    sell = [
        pp.CashMovement(T0 + H4 * 3, Decimal(240)),
        pp.CashMovement(T0 + H4 * 3, Decimal("-0.24")),
    ]
    fills = [("buy", Decimal(2), T0 + H4), ("sell", Decimal(2), T0 + H4 * 3)]

    curve = pp.live_equity_curve(
        _closes("100", "100", "110", "120", "90"), [deposit, *buy, *sell], fills
    )

    assert [value for _, value in curve] == [
        Decimal(1000),  # flat
        Decimal("999.8"),  # 800 - 0.2 cash + 2 x 100
        Decimal("1019.8"),  # marked at 110
        Decimal("1039.56"),  # sold at 120: all cash
        Decimal("1039.56"),  # flat again, the later price no longer matters
    ]


def test_a_portfolio_sums_the_accounts_at_the_closes_they_share() -> None:
    a = [(T0, Decimal(100)), (T0 + H4, Decimal(110))]
    b = [(T0, Decimal(200)), (T0 + H4, Decimal(190)), (T0 + H4 * 2, Decimal(195))]

    assert pp.combine_curves([a, b]) == [(T0, Decimal(300)), (T0 + H4, Decimal(300))]
    assert pp.combine_curves([]) == []


def test_the_summary_counts_the_start_as_the_first_peak() -> None:
    # Fell straight from the starting 1000 to 950 and recovered to 1020.
    curve = [(T0, Decimal(950)), (T0 + H4, Decimal(1020))]

    summary = pp.summarize(curve, Decimal(1000))

    assert summary.return_pct == pytest.approx(0.02)
    assert summary.max_drawdown_pct == pytest.approx(0.05)


def test_an_empty_curve_summarizes_to_no_change() -> None:
    assert pp.summarize([], Decimal(1000)) == pp.CurveSummary(return_pct=0.0, max_drawdown_pct=0.0)


def test_closes_and_wins_count_pnl_before_fees() -> None:
    counts = pp.count_trades([Decimal(5), Decimal(-3), Decimal("0.01")])
    assert (counts.closes, counts.wins) == (3, 2)


def test_historical_windows_fit_inside_the_history() -> None:
    first = datetime(2026, 1, 1, tzinfo=UTC)
    starts = pp.window_starts(
        first, first + timedelta(days=30), length=timedelta(days=10), stride=timedelta(days=7)
    )
    assert starts == [first + timedelta(days=d) for d in (0, 7, 14)]


def test_window_length_and_stride_must_be_positive() -> None:
    with pytest.raises(ValueError):
        pp.window_starts(T0, T0 + H4, length=timedelta(0), stride=H4)


def test_the_expected_range_is_the_5_50_95_points() -> None:
    samples = [float(i) for i in range(101)]  # 0..100

    result = pp.expected_range(samples)

    assert (result.low, result.median, result.high, result.samples) == (5.0, 50.0, 95.0, 101)
    assert pp.share_at_or_below(samples, 10.0) == pytest.approx(11 / 101)


def test_an_empty_history_cannot_give_a_range() -> None:
    with pytest.raises(ValueError):
        pp.expected_range([])


def test_a_drawdown_beyond_the_95_point_warns_to_consider_stopping() -> None:
    returns = pp.ExpectedRange(low=-0.02, median=0.01, high=0.04, samples=100)
    drawdowns = pp.ExpectedRange(low=0.0, median=0.01, high=0.03, samples=100)

    warnings = pp.range_warnings(
        pp.CurveSummary(return_pct=-0.03, max_drawdown_pct=0.04), returns, drawdowns
    )

    assert len(warnings) == 2
    assert "停止を検討" in warnings[0]
    assert "5%" in warnings[1]


def test_values_inside_the_range_raise_no_warning() -> None:
    returns = pp.ExpectedRange(low=-0.02, median=0.01, high=0.04, samples=100)
    drawdowns = pp.ExpectedRange(low=0.0, median=0.01, high=0.03, samples=100)

    assert pp.range_warnings(pp.CurveSummary(0.0, 0.02), returns, drawdowns) == []
