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

**`exit_policy`** (Unit 3): `signal` (the default -- exit only on an opposing
signal) or `stop_loss` (also exit at the stop the Risk Gate sized the entry
for, letting winners run until the signal exits; see
`docs/plans/paper-trading-live-data.md`). The backtest's `stop_and_target` is
refused: a fixed take-profit was evaluated and rejected, so no live bot runs it.

**Only validated strategies resolve**: `dummy_sma_crossover` (the SMA pipeline
skeleton) was rejected in research and is refused here since 2026-10-02 (user
decision), so a bot still stored with it cannot start. The SMA generator itself
stays in `research_strategies.py` as a research baseline.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.models.market_data import Candle
from app.trading.application.donchian_breakout_signal import generate_donchian_breakout_signal
from app.trading.application.dummy_signal import DummySignalAction


class UnresolvableStrategyError(ValueError):
    pass


LiveExitPolicy = Literal["signal", "stop_loss"]
_LIVE_EXIT_POLICIES: tuple[LiveExitPolicy, ...] = ("signal", "stop_loss")


@dataclass(frozen=True)
class LiveStrategy:
    kind: str
    parameters: dict[str, int]
    generate: Callable[[Sequence[Candle]], DummySignalAction]
    exit_policy: LiveExitPolicy

    def rationale(self, latest_candle: Candle) -> dict[str, object]:
        """What a `Signal` row records about how it was produced."""
        return {
            "kind": self.kind,
            "parameters": dict(self.parameters),
            "exit_policy": self.exit_policy,
            "close": str(latest_candle.close),
        }


def _positive_int(definition: Mapping[str, Any], key: str) -> int:
    value = definition.get(key)
    # bool is an int subclass; True must not pass as a period of 1.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise UnresolvableStrategyError(f"'{key}' must be a positive integer, got {value!r}")
    return value


def _exit_policy(definition: Mapping[str, Any]) -> LiveExitPolicy:
    if "exit_policy" not in definition:
        return "signal"
    value = definition["exit_policy"]
    for policy in _LIVE_EXIT_POLICIES:
        if value == policy:
            return policy
    raise UnresolvableStrategyError(f"exit_policy {value!r} is not supported for live bots")


def resolve_live_strategy(definition: Mapping[str, Any] | None) -> LiveStrategy:
    if not isinstance(definition, Mapping):
        raise UnresolvableStrategyError("strategy definition is missing")
    exit_policy = _exit_policy(definition)
    kind = definition.get("kind")
    if kind == "donchian_breakout":
        entry = _positive_int(definition, "entry_period")
        exit_ = _positive_int(definition, "exit_period")

        # Named, not a lambda: a backtest stores the generator's __name__ in its parameters.
        def donchian_breakout(candles: Sequence[Candle]) -> DummySignalAction:
            return generate_donchian_breakout_signal(candles, entry_period=entry, exit_period=exit_)

        return LiveStrategy(
            kind=kind,
            parameters={"entry_period": entry, "exit_period": exit_},
            generate=donchian_breakout,
            exit_policy=exit_policy,
        )
    raise UnresolvableStrategyError(f"unknown strategy kind {kind!r}")
