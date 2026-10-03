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


def test_the_rejected_sma_skeleton_is_refused_so_its_bots_cannot_start() -> None:
    # The exact definition the SMA pipeline skeleton's strategy version is stored with.
    definition = {
        "kind": "dummy_sma_crossover",
        "period": 5,
        "note": "pipeline skeleton only, not a real strategy -- see dummy_signal.py",
    }
    with pytest.raises(ls.UnresolvableStrategyError, match="dummy_sma_crossover"):
        ls.resolve_live_strategy(definition)


def test_a_donchian_definition_uses_its_own_periods() -> None:
    strategy = ls.resolve_live_strategy(
        {"kind": "donchian_breakout", "entry_period": 3, "exit_period": 2}
    )
    candles = _candles([100, 100, 100, 100, 101])

    assert (
        strategy.generate(candles)
        == generate_donchian_breakout_signal(candles, entry_period=3, exit_period=2)
        == "buy"
    )
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
    ],
)
def test_anything_unresolvable_is_refused(definition: object) -> None:
    with pytest.raises(ls.UnresolvableStrategyError):
        ls.resolve_live_strategy(definition)  # type: ignore[arg-type]


# ---- exit_policy (docs/plans/paper-trading-live-data.md Unit 3) ----


def test_exit_policy_defaults_to_signal_only() -> None:
    strategy = ls.resolve_live_strategy(
        {"kind": "donchian_breakout", "entry_period": 55, "exit_period": 20}
    )
    assert strategy.exit_policy == "signal"


def test_a_stop_loss_exit_policy_is_carried_and_recorded() -> None:
    strategy = ls.resolve_live_strategy(
        {
            "kind": "donchian_breakout",
            "entry_period": 55,
            "exit_period": 20,
            "exit_policy": "stop_loss",
        }
    )
    assert strategy.exit_policy == "stop_loss"
    assert strategy.rationale(_candles([100])[-1])["exit_policy"] == "stop_loss"


@pytest.mark.parametrize("exit_policy", ["stop_and_target", "trailing", 1, None])
def test_exit_policies_not_supported_live_are_refused(exit_policy: object) -> None:
    with pytest.raises(ls.UnresolvableStrategyError):
        ls.resolve_live_strategy(
            {
                "kind": "donchian_breakout",
                "entry_period": 55,
                "exit_period": 20,
                "exit_policy": exit_policy,
            }
        )
