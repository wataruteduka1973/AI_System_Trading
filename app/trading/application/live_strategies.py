"""Picks a live/paper bot's signal generator from its `StrategyVersion.definition`
(docs/plans/paper-trading-live-data.md Unit 2).

**Resolved from the stored definition, not from `research_strategies.py`**: the
research list is keyed by display names the scripts print, while a bot's
strategy version is a stored, versioned record -- the definition saved with
the version is what decides what the bot runs, so the same version always
means the same generator and parameters.

**Refuse rather than guess**: an unknown `kind`, a missing parameter, or one of
the wrong type raises `UnresolvableStrategyError`. Bot startup checks this
before a bot can run (`bot_lifecycle.validate_bot_startup`), and evaluation
raises it again if a definition ever stops resolving.

**`records_take_profit`**: the pipeline stores an informational take-profit
price on the order intent (never executed). Only the dummy SMA pipeline was
designed around one; the Donchian strategy exits on its own channel and a
stop-loss, so recording a target for it would describe an exit it never takes.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.market_data import Candle
from app.trading.application.donchian_breakout_signal import generate_donchian_breakout_signal
from app.trading.application.dummy_signal import DummySignalAction, generate_dummy_signal


class UnresolvableStrategyError(ValueError):
    pass


@dataclass(frozen=True)
class LiveStrategy:
    kind: str
    parameters: dict[str, int]
    generate: Callable[[Sequence[Candle]], DummySignalAction]
    records_take_profit: bool

    def rationale(self, latest_candle: Candle) -> dict[str, object]:
        """What a `Signal` row records about how it was produced."""
        return {
            "kind": self.kind,
            "parameters": dict(self.parameters),
            "close": str(latest_candle.close),
        }


def _positive_int(definition: Mapping[str, Any], key: str) -> int:
    value = definition.get(key)
    # bool is an int subclass; True must not pass as a period of 1.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise UnresolvableStrategyError(f"'{key}' must be a positive integer, got {value!r}")
    return value


def resolve_live_strategy(definition: Mapping[str, Any] | None) -> LiveStrategy:
    if not isinstance(definition, Mapping):
        raise UnresolvableStrategyError("strategy definition is missing")
    kind = definition.get("kind")
    if kind == "dummy_sma_crossover":
        period = _positive_int(definition, "period")
        return LiveStrategy(
            kind=kind,
            parameters={"period": period},
            generate=lambda candles: generate_dummy_signal(candles, period=period),
            records_take_profit=True,
        )
    if kind == "donchian_breakout":
        entry = _positive_int(definition, "entry_period")
        exit_ = _positive_int(definition, "exit_period")
        return LiveStrategy(
            kind=kind,
            parameters={"entry_period": entry, "exit_period": exit_},
            generate=lambda candles: generate_donchian_breakout_signal(
                candles, entry_period=entry, exit_period=exit_
            ),
            records_take_profit=False,
        )
    raise UnresolvableStrategyError(f"unknown strategy kind {kind!r}")
