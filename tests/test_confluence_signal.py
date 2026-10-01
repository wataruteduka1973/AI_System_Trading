"""`app/trading/application/confluence_signal.py` (docs/plans/confluence-filters.md).

The precomputed indicator table is checked against the existing, already-tested
indicator functions applied to each bar's own prefix of the series -- which is
also what proves the table never uses a later bar. The filters are checked with
hand-built tables so each blocked/allowed case is explicit."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.market_data.application.indicators import (
    average_true_range,
    exponential_moving_average,
    relative_strength_index,
)
from app.models.market_data import Candle
from app.trading.application import confluence_signal as cs


def _candle(i: int, close: float, spread: float = 1.0) -> Candle:
    t = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=4 * i)
    price = Decimal(str(close))
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="4h",
        open_time=t,
        close_time=t + timedelta(hours=4),
        open=price,
        high=price + Decimal(str(spread)),
        low=price - Decimal(str(spread)),
        close=price,
        source="test",
        is_final=True,
    )


_CLOSES = [100, 102, 101, 105, 104, 108, 103, 99, 101, 106, 110, 107, 111, 115, 112, 109]


def test_table_matches_the_indicators_computed_on_each_bars_own_prefix() -> None:
    candles = [_candle(i, c, spread=1 + (i % 3)) for i, c in enumerate(_CLOSES)]
    table = cs.build_indicator_table(
        candles, trend_period=5, atr_period=3, rsi_period=3, volatility_window=4
    )

    for i, candle in enumerate(candles):
        prefix = candles[: i + 1]
        snapshot = table[candle.open_time]
        ema = exponential_moving_average(prefix, 5)
        atr = average_true_range(prefix, 3)
        rsi = relative_strength_index(prefix, 3)
        assert snapshot.trend_ema == (None if ema is None else pytest.approx(float(ema)))
        assert snapshot.atr_pct == (
            None if atr is None else pytest.approx(float(atr / candle.close))
        )
        assert snapshot.rsi == (None if rsi is None else pytest.approx(float(rsi)))


def test_volatility_median_needs_a_full_window_of_atr_values() -> None:
    candles = [_candle(i, c) for i, c in enumerate(_CLOSES)]
    table = cs.build_indicator_table(
        candles, trend_period=5, atr_period=3, rsi_period=3, volatility_window=4
    )
    atr_pcts = [table[c.open_time].atr_pct for c in candles]
    first_atr = next(i for i, value in enumerate(atr_pcts) if value is not None)

    for i, candle in enumerate(candles):
        median = table[candle.open_time].atr_pct_median
        if i < first_atr + 3:
            assert median is None
        else:
            window = sorted(v for v in atr_pcts[i - 3 : i + 1] if v is not None)
            assert median == pytest.approx((window[1] + window[2]) / 2)


# ---- filters over a Donchian(3/2) base signal ----
#
# closes 100,100,100,100,101: bar 4 is the first close above the prior 3 highs,
# so the unfiltered base signal is "buy" there.

_BREAKOUT = [_candle(i, c, spread=0) for i, c in enumerate([100, 100, 100, 100, 101])]


def _table(**snapshot: float | None) -> dict[datetime, cs.IndicatorSnapshot]:
    values: dict[str, float | None] = {
        "trend_ema": 90.0,
        "atr_pct": 0.01,
        "atr_pct_median": 0.02,
        "rsi": 50.0,
    }
    values.update(snapshot)
    return {_BREAKOUT[-1].open_time: cs.IndicatorSnapshot(**values)}


def _signal(filters: cs.ConfluenceFilters, table: dict[datetime, cs.IndicatorSnapshot]) -> str:
    generator = cs.make_confluence_signal(table, filters, entry_period=3, exit_period=2)
    return generator(_BREAKOUT)


def test_no_filters_passes_the_base_buy_through() -> None:
    assert _signal(cs.NO_FILTERS, _table()) == "buy"


@pytest.mark.parametrize(
    ("filters", "blocking_snapshot"),
    [
        (cs.ConfluenceFilters(trend=True), {"trend_ema": 102.0}),
        (cs.ConfluenceFilters(volatility="calm"), {"atr_pct": 0.03}),
        (cs.ConfluenceFilters(volatility="expanding"), {"atr_pct": 0.01}),
        (cs.ConfluenceFilters(momentum_cap=True), {"rsi": 80.0}),
    ],
)
def test_each_filter_blocks_a_buy_that_fails_it(
    filters: cs.ConfluenceFilters, blocking_snapshot: dict[str, float]
) -> None:
    assert _signal(filters, _table(**blocking_snapshot)) == "hold"


@pytest.mark.parametrize(
    "filters",
    [
        cs.ConfluenceFilters(trend=True),
        cs.ConfluenceFilters(volatility="calm"),
        cs.ConfluenceFilters(volatility="expanding"),
        cs.ConfluenceFilters(momentum_cap=True),
    ],
)
def test_a_filter_whose_indicator_is_not_ready_blocks_the_buy(
    filters: cs.ConfluenceFilters,
) -> None:
    table = _table(trend_ema=None, atr_pct=None, atr_pct_median=None, rsi=None)
    assert _signal(filters, table) == "hold"


def test_a_bar_missing_from_the_table_never_buys() -> None:
    assert _signal(cs.ConfluenceFilters(trend=True), {}) == "hold"


def test_filters_never_block_a_sell() -> None:
    # Donchian(3/2): 110 then 105 breaks below the prior 2 lows -> base "sell".
    candles = [_candle(i, c, spread=0) for i, c in enumerate([100, 100, 100, 100, 110, 108, 105])]
    all_filters = cs.ConfluenceFilters(trend=True, volatility="calm", momentum_cap=True)
    generator = cs.make_confluence_signal({}, all_filters, entry_period=3, exit_period=2)
    assert generator(candles) == "sell"


def test_the_twelve_combinations_start_with_no_filters_and_are_distinct() -> None:
    combos = cs.ALL_FILTER_COMBINATIONS
    assert len(combos) == 12
    assert combos[0] == cs.NO_FILTERS
    assert len({c.name for c in combos}) == 12
