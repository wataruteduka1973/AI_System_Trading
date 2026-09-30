"""`app/trading/application/backtest_robustness.py`: the chained equity curve
and the risk/robustness measures computed from it. Curves are hand-built so
every expected value can be checked by hand."""

from datetime import UTC, datetime, timedelta

import pytest
from app.trading.application import backtest_robustness as rb


def _t(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day, hours=hour)


def test_chain_compounds_each_window_from_where_the_previous_one_ended() -> None:
    windows = [
        # (equity curve as fractions of that window's own starting equity, ending fraction)
        ([(_t(0), 1.0), (_t(1), 1.1)], 1.1),
        ([(_t(2), 1.0), (_t(3), 0.5)], 0.5),
    ]
    curve = rb.chain_windows(windows)
    assert [value for _, value in curve] == pytest.approx([1.0, 1.1, 1.1, 1.1, 0.55, 0.55])
    assert curve[-1][1] == pytest.approx(0.55)


def test_max_drawdown_measures_the_deepest_fall_from_a_prior_peak() -> None:
    curve = [(_t(0), 1.0), (_t(1), 1.2), (_t(2), 0.9), (_t(3), 1.3), (_t(4), 1.04)]
    drawdown = rb.max_drawdown(curve)
    assert drawdown.depth == pytest.approx(0.25)  # 1.2 -> 0.9
    assert drawdown.peak_time == _t(1)
    assert drawdown.trough_time == _t(2)


def test_longest_underwater_is_the_longest_stretch_below_a_prior_peak() -> None:
    curve = [(_t(0), 1.0), (_t(1), 0.9), (_t(5), 1.0), (_t(6), 0.95), (_t(8), 1.1)]
    # 1.0 at day 0 is not regained until day 5 (5 days); the second dip lasts 2 days.
    assert rb.longest_underwater(curve) == timedelta(days=5)


def test_longest_underwater_counts_an_unrecovered_drawdown_to_the_end() -> None:
    curve = [(_t(0), 1.0), (_t(1), 0.8), (_t(10), 0.9)]
    assert rb.longest_underwater(curve) == timedelta(days=10)


def test_worst_daily_return_uses_each_days_last_value() -> None:
    curve = [(_t(0, 1), 1.0), (_t(0, 20), 1.0), (_t(1, 3), 0.7), (_t(1, 22), 0.9), (_t(2), 0.9)]
    # day 0 closes at 1.0, day 1 closes at 0.9: -10% (the intraday 0.7 is not a close)
    assert rb.worst_daily_return(curve) == pytest.approx(-0.1)


def test_window_return_spans_the_last_value_before_the_start_to_the_last_value_in_it() -> None:
    curve = [(_t(0), 1.0), (_t(1), 1.0), (_t(2), 0.8), (_t(3), 0.85), (_t(4), 2.0)]
    assert rb.window_return(curve, start=_t(2), end=_t(3)) == pytest.approx(-0.15)


def test_window_return_is_none_without_values_on_both_sides() -> None:
    curve = [(_t(5), 1.0), (_t(6), 1.1)]
    assert rb.window_return(curve, start=_t(0), end=_t(1)) is None


def test_top_share_is_how_much_of_the_total_the_best_items_account_for() -> None:
    assert rb.top_share([50.0, 30.0, 10.0, -20.0], top_n=1) == pytest.approx(50 / 70)


def test_top_share_is_none_when_the_total_is_not_positive() -> None:
    assert rb.top_share([10.0, -20.0], top_n=1) is None
