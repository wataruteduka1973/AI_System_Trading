"""`app/trading/application/live_strategies.py` (docs/plans/paper-trading-live-data.md
Unit 2): a bot's `StrategyVersion.definition` picks its signal generator, and
anything that cannot be resolved unambiguously is refused rather than guessed."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models.market_data import Candle
from app.trading.application import live_strategies as ls
from app.trading.application.donchian_breakout_signal import generate_donchian_breakout_signal
from app.trading.application.dummy_signal import generate_dummy_signal


def _candles(closes: list[int]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
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


def test_the_existing_dummy_definition_keeps_its_behaviour_and_take_profit() -> None:
    # The exact definition `ensure_dummy_strategy_and_risk_profile` stores, note included.
    definition = {"kind": "dummy_sma_crossover", "period": 5, "note": "pipeline skeleton only"}
    strategy = ls.resolve_live_strategy(definition)
    candles = _candles([100, 100, 100, 100, 100, 110])

    assert strategy.generate(candles) == generate_dummy_signal(candles, period=5) == "buy"
    assert strategy.records_take_profit is True
    assert strategy.rationale(candles[-1]) == {
        "kind": "dummy_sma_crossover",
        "parameters": {"period": 5},
        "close": "110",
    }


def test_a_donchian_definition_uses_its_own_periods_and_no_take_profit() -> None:
    strategy = ls.resolve_live_strategy(
        {"kind": "donchian_breakout", "entry_period": 3, "exit_period": 2}
    )
    candles = _candles([100, 100, 100, 100, 101])

    assert (
        strategy.generate(candles)
        == generate_donchian_breakout_signal(candles, entry_period=3, exit_period=2)
        == "buy"
    )
    assert strategy.records_take_profit is False
    assert strategy.rationale(candles[-1])["parameters"] == {"entry_period": 3, "exit_period": 2}


@pytest.mark.parametrize(
    "definition",
    [
        None,
        {},
        {"kind": "rsi_mean_reversion"},
        {"kind": "donchian_breakout", "entry_period": 55},
        {"kind": "donchian_breakout", "entry_period": "55", "exit_period": 20},
        {"kind": "donchian_breakout", "entry_period": 0, "exit_period": 20},
        {"kind": "donchian_breakout", "entry_period": True, "exit_period": 20},
        {"kind": "dummy_sma_crossover", "period": -1},
    ],
)
def test_anything_unresolvable_is_refused(definition: object) -> None:
    with pytest.raises(ls.UnresolvableStrategyError):
        ls.resolve_live_strategy(definition)  # type: ignore[arg-type]
