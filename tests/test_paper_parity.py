"""`app/trading/application/paper_parity.py` (docs/plans/paper-trading-live-data.md
Unit 4): checks that a paper bot decided what the backtest would have decided on
the same bars, and finds bars it never evaluated (e.g. the machine was asleep)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models.market_data import Candle
from app.trading.application import paper_parity as pp


def _candles(closes: list[int]) -> list[Candle]:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    return [
        Candle(
            id=uuid4(),
            instrument_id=uuid4(),
            timeframe="4h",
            open_time=start + timedelta(hours=4 * i),
            close_time=start + timedelta(hours=4 * (i + 1)),
            open=Decimal(c),
            high=Decimal(c),
            low=Decimal(c),
            close=Decimal(c),
            source="test",
            is_final=True,
        )
        for i, c in enumerate(closes)
    ]


def _breakout(history: list[Candle]) -> str:
    """A stand-in generator: buy when the latest close is the highest so far."""
    return "buy" if history[-1].close > max(c.close for c in history[:-1]) else "hold"


def test_recorded_signals_that_match_a_recomputation_are_reported_as_matching() -> None:
    candles = _candles([100, 100, 101, 101])
    recorded = {candles[2].id: "buy", candles[3].id: "hold"}

    result = pp.compare_signals(candles, recorded, _breakout, history_window=260)

    assert result.matched == 2
    assert result.mismatches == []


def test_a_recorded_signal_that_differs_from_the_recomputation_is_a_mismatch() -> None:
    candles = _candles([100, 100, 101, 101])
    recorded = {candles[2].id: "hold"}

    result = pp.compare_signals(candles, recorded, _breakout, history_window=260)

    assert result.matched == 0
    [mismatch] = result.mismatches
    assert (mismatch.candle_open_time, mismatch.recorded, mismatch.recomputed) == (
        candles[2].open_time, "hold", "buy",
    )  # fmt: skip


def test_the_recomputation_sees_only_the_history_window_ending_at_that_bar() -> None:
    candles = _candles([105, 100, 100, 101])
    # With a 3-bar window ending at bar 3, the 105 is out of view -> "buy".
    result = pp.compare_signals(candles, {candles[3].id: "buy"}, _breakout, history_window=3)
    assert result.matched == 1


def test_bars_closed_while_running_but_never_evaluated_are_reported() -> None:
    candles = _candles([100, 100, 100, 100, 100])
    running_since = candles[1].close_time  # bars 2..4 closed while running
    evaluated = {candles[2].id, candles[4].id}

    missed = pp.find_unevaluated_bars(candles, evaluated, running_since=running_since)

    assert [c.open_time for c in missed] == [candles[3].open_time]


# ---- missed-bar effect (spot, long-only) ----


@pytest.mark.parametrize(
    ("action", "held", "effect"),
    [
        ("buy", Decimal(0), "entry"),
        ("buy", Decimal("0.5"), "addition"),
        ("sell", Decimal("0.5"), "exit"),
        # Regression: a sell with nothing held was flagged as "an entry/exit the bot
        # never took", though the Risk Gate refuses it on spot (binance_no_short).
        ("sell", Decimal(0), None),
        ("hold", Decimal(0), None),
        ("hold", Decimal("0.5"), None),
    ],
)
def test_a_missed_signal_counts_only_if_it_would_have_become_an_order(
    action: str, held: Decimal, effect: str | None
) -> None:
    assert pp.missed_bar_effect(action, held) == effect


def test_the_held_quantity_is_rebuilt_from_fills_up_to_that_time() -> None:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    fills = [
        ("buy", Decimal("1.0"), start),
        ("sell", Decimal("0.4"), start + timedelta(hours=8)),
        ("sell", Decimal("0.6"), start + timedelta(hours=16)),
    ]

    assert pp.held_quantity_at(fills, start - timedelta(hours=1)) == 0
    assert pp.held_quantity_at(fills, start) == Decimal("1.0")
    assert pp.held_quantity_at(fills, start + timedelta(hours=8)) == Decimal("0.6")
    assert pp.held_quantity_at(fills, start + timedelta(hours=20)) == 0
