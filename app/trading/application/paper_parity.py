"""Checks a paper bot against the backtest on the same bars
(docs/plans/paper-trading-live-data.md Unit 4). Used by
`scripts/check_paper_parity.py`.

- `compare_signals`: every `Signal` a bot recorded is recomputed from the same
  bars the live pipeline read (the `history_window` bars ending at that bar). A
  mismatch means live and backtest are not running the same decision -- the
  whole point of the paper run is that they are.
- `find_unevaluated_bars`: bars that closed while the bot was running but got
  no signal. The pipeline evaluates only the latest bar, so a bar missed while
  the worker was down never produces its entry (stops are still caught later);
  these are the places live results can drift from the backtest.
- `missed_bar_effect`: whether a missed bar's signal would actually have become an
  order, given what the bot held then. Only those are worth flagging; a spot sell
  with nothing held would have been refused anyway.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.models.market_data import Candle


@dataclass(frozen=True)
class SignalMismatch:
    candle_open_time: datetime
    recorded: str
    recomputed: str


@dataclass(frozen=True)
class SignalComparison:
    matched: int
    mismatches: list[SignalMismatch] = field(default_factory=list)


def compare_signals(
    candles: Sequence[Candle],
    recorded: Mapping[UUID, str],
    generate: Callable[[Sequence[Candle]], str],
    *,
    history_window: int,
) -> SignalComparison:
    """`candles` ascending; `recorded` maps a candle id to the action the bot
    recorded on it. Candles without a recorded signal are skipped."""
    matched = 0
    mismatches: list[SignalMismatch] = []
    for index, candle in enumerate(candles):
        action = recorded.get(candle.id)
        if action is None:
            continue
        history = candles[max(0, index + 1 - history_window) : index + 1]
        recomputed = generate(history)
        if recomputed == action:
            matched += 1
        else:
            mismatches.append(SignalMismatch(candle.open_time, action, recomputed))
    return SignalComparison(matched=matched, mismatches=mismatches)


def find_unevaluated_bars(
    candles: Sequence[Candle], evaluated: set[UUID], *, running_since: datetime
) -> list[Candle]:
    """Bars that closed after `running_since` with no recorded signal."""
    return [c for c in candles if c.close_time > running_since and c.id not in evaluated]


MissedEffect = Literal["entry", "addition", "exit"]


def held_quantity_at(fills: Sequence[tuple[str, Decimal, datetime]], at: datetime) -> Decimal:
    """The long quantity a spot bot held at `at`, from its (side, quantity, executed_at)
    fills up to and including `at`."""
    return sum(
        (
            quantity if side == "buy" else -quantity
            for side, quantity, executed in fills
            if executed <= at
        ),
        Decimal(0),
    )


def missed_bar_effect(action: str, held_quantity: Decimal) -> MissedEffect | None:
    """What a signal on a missed bar would have done to a spot (long-only) position, or
    None if it would not have become an order. A sell with nothing held is refused by
    the Risk Gate (`binance_no_short`), so missing it changes nothing."""
    if action == "buy":
        return "addition" if held_quantity > 0 else "entry"
    if action == "sell" and held_quantity > 0:
        return "exit"
    return None
