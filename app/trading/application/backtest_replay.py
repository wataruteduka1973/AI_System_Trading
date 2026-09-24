"""Backtest replay harness (docs/plans/horizon4-lite-backtest.md Unit 4).

Walks a candle series bar by bar, calling the same signal-generation shape and the
same `risk_gate._evaluate_conservative_v1`/`backtest_fill` pure functions Units 2-3
built, against in-memory state instead of DB rows -- no `Session`, no
`TradingAccount`/`TradingPosition`/`Signal`/`RiskDecision` row is read or written
here. `run_replay`'s output (`TradeRecord`s) is what a caller turns into
`backtest_trade` rows; this module does not do that persistence itself, matching
Units 1-3's own DB-free, directly-testable shape.

**Reuses `risk_gate`'s private helpers on purpose** (`RiskState`,
`_evaluate_conservative_v1`, `_stop_distance`, `_fee_buffer_per_unit`,
`_ATR_HISTORY_CANDLES`): that module's own docstring (Unit 2 refactor note) states
this is exactly what they are for -- "the eventual backtest harness (Unit 3/4)
builds its own `RiskState` instead of adding a second implementation of this
function." Importing the leading-underscore names directly (rather than renaming
them to public) was chosen over renaming to avoid touching Unit 2's already-tested
code; `_stop_distance` in particular collides with a local variable of the same name
inside `risk_gate.evaluate_signal`; renaming it there would have needed the local
renamed too, an unrelated-seeming edit to unit 2's file this task did not ask for.

**Dote-gating is reproduced here** (2026-09-20, per the user's explicit
confirmation): when a bar's signal opposes the currently held position, this
harness places a close-only fill (quantity capped to exactly the held quantity) and
does not evaluate a new/reverse entry in the same bar -- copied from
`dummy_pipeline.run_dummy_pipeline_once`'s "Dote-gating" behavior, so a backtest
run's execution *policy* matches the live pipeline's, not just its signal/risk
logic. A structural consequence of always closing the *exact* held quantity: this
pipeline's dote-gating path never triggers `apply_fill_to_position`'s partial-reduce
or flip branches (those remain reachable in general, and are still handled
correctly below, in case a future signal source bypasses dote-gating).

**`generate_dummy_signal` is the default `signal_generator`** (2026-09-20, per the
user's explicit confirmation): its own module docstring said "Do not... backtest...
this" when written, because no backtest capability existed yet to point at. ADR 0003
is that capability, and generating conservative-v1-compliant non-AI-baseline trades
to compare against Chronos is exactly what this replay harness needs it for; that
docstring has been updated (see dummy_signal.py) rather than left to describe a
scope that no longer applies. `signal_generator` is a parameter specifically so a
Chronos-backed generator can replace it later without touching this module.

**Equity model**: `_ReplayState.cash_equity` is a running total of every fill's
notional (+ for a sell, - for a buy) minus its fee -- algebraically the same
`ledger_entry(entry_type in ('cash','fee'))` balance `account_valuation.compute_equity`
sums for the live path (`realized_pnl` ledger entries are informational only there,
never summed into cash -- see that module's docstring -- so this mirrors it exactly
without needing a `realized_pnl`-shaped entry here at all). At any bar, "equity" fed
into `RiskState` is `cash_equity` plus the *unrealized* P&L of the currently open
position marked at that bar's close, matching `compute_equity`'s own definition
(cash + unrealized P&L of the one open position).

**`day_start_equity`/`week_start_equity`** are captured once, on the first bar whose
`close_time.date()` falls in a new day/ISO week, using that bar's own mark-to-market
equity -- a precise analogue of the live path's `_equity_at(window_start)` (which
finds the *first* `account_snapshot` on/after the window boundary; a backtest has no
snapshot table, so "the first bar of the window" stands in for it exactly, with no
snapshot-timing gap).

**`now` for `RiskState` purposes is each bar's own `close_time`**, not a wall clock:
a replay processes history instantaneously, so there is no meaningful "how stale is
this data right now" to measure -- `data_delay` is therefore always 0 bars in a
backtest, which correctly reflects that a replay's data is never stale (this is not
an approximation the way spread/slippage is; it is what "delay" means once there is
no wall clock).

**Spread/slippage approximation (ADR 0003, known limitation)**: `spread` is a single
value supplied for the whole run (there is no historical tick/spread series to look
up per bar) and is turned into `expected_slippage` using the exact same per-exchange
formula `risk_gate.evaluate_signal` uses inline, so risk-sizing and the simulated
fill price stay internally consistent for a given bar.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from app.exchanges.types import TIMEFRAME_SECONDS
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application import backtest_fill as fill_sim
from app.trading.application import order_flow, risk_gate
from app.trading.application.dummy_signal import DummySignalAction, generate_dummy_signal

BacktestSignalGenerator = Callable[[Sequence[Candle]], DummySignalAction]

_HISTORY_WINDOW = risk_gate._ATR_HISTORY_CANDLES
"""Bound on how much of `candles` `run_replay` hands a bar's `signal_generator`
(and, downstream, the ATR calculation) -- /code-review finding: `history =
candles[:i+1]` used to copy a growing, unbounded prefix on every one of an N-bar
run's iterations (O(N^2) total). 100 matches `risk_gate._ATR_HISTORY_CANDLES`,
the largest lookback any current consumer needs (`generate_dummy_signal`'s
default `period` is 5). A custom `signal_generator` that needs a longer lookback
than this is not supported by `run_replay` today -- raise this constant (not
just the call-site slice) if one is added, since the ATR window below relies on
the same bound."""


@dataclass(frozen=True)
class TradeRecord:
    """One closed/reduced/flipped position leg -- shaped to become one
    `backtest_trade` row (`app/models/backtest.py`, Unit 1)."""

    sequence_no: int
    side: Literal["buy", "sell"]
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    fees: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class ReplayResult:
    trades: list[TradeRecord]
    ending_equity: Decimal
    ending_position: fill_sim.BacktestPosition | None
    equity_curve: list[tuple[datetime, Decimal]]
    """Mark-to-market equity at the close of every bar processed (Unit 5's max
    drawdown needs this; it is the same value each bar feeds into `RiskState.equity`
    /`peak_equity`, just also kept here instead of being discarded after the loop)."""


@dataclass
class _ReplayState:
    cash_equity: Decimal
    position: fill_sim.BacktestPosition | None = None
    position_opened_at: datetime | None = None
    day_start_date: date | None = None
    day_start_equity: Decimal = Decimal(0)
    week_start_date: date | None = None
    week_start_equity: Decimal = Decimal(0)
    peak_equity: Decimal = Decimal(0)
    consecutive_losses: int = 0
    last_order_time: datetime | None = None
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    next_sequence_no: int = 1


def _unrealized_pnl(position: fill_sim.BacktestPosition | None, price: Decimal) -> Decimal:
    if position is None:
        return Decimal(0)
    direction = Decimal(1) if position.side == "long" else Decimal(-1)
    return (price - position.average_entry_price) * position.quantity * direction


def _expected_slippage(exchange_code: str, spread: Decimal) -> Decimal:
    """Mirrors `risk_gate.evaluate_signal`'s formula -- see module docstring's
    "Spread/slippage approximation" note. Both now read the same shared
    `order_flow.SLIPPAGE_COEFFICIENT_BY_EXCHANGE` table (/code-review finding:
    this function and risk_gate.py previously hardcoded their own copies of
    these two numbers, which could silently drift apart)."""
    return spread * order_flow.SLIPPAGE_COEFFICIENT_BY_EXCHANGE.get(exchange_code, Decimal(0))


def _apply_and_record(
    state: _ReplayState,
    pre_fill_position: fill_sim.BacktestPosition | None,
    fill: fill_sim.SimulatedFill,
    order_side: fill_sim.OrderSide,
    now: datetime,
    *,
    allow_short: bool,
) -> None:
    outcome = fill_sim.apply_fill_to_position(
        pre_fill_position, fill, order_side, allow_short=allow_short
    )
    notional = fill.price * fill.quantity
    state.cash_equity += (notional if order_side == "sell" else -notional) - fill.fee_amount

    if outcome.realized_pnl != 0:
        assert pre_fill_position is not None
        assert state.position_opened_at is not None
        closing_quantity = min(fill.quantity, pre_fill_position.quantity)
        state.trades.append(
            TradeRecord(
                sequence_no=state.next_sequence_no,
                side=order_side,
                entry_time=state.position_opened_at,
                exit_time=now,
                entry_price=pre_fill_position.average_entry_price,
                exit_price=fill.price,
                quantity=closing_quantity,
                fees=fill.fee_amount,
                realized_pnl=outcome.realized_pnl,
            )
        )
        state.next_sequence_no += 1
        state.consecutive_losses = state.consecutive_losses + 1 if outcome.realized_pnl < 0 else 0

    opened_from_flat = pre_fill_position is None and outcome.position is not None
    flipped = (
        outcome.position is not None
        and pre_fill_position is not None
        and outcome.position.side != pre_fill_position.side
    )
    if opened_from_flat or flipped:
        state.position_opened_at = now
    elif outcome.position is None:
        state.position_opened_at = None
    # else: same-direction increase or partial reduce -- opened_at unchanged,
    # matching order_flow._apply_fill_to_position's own opened_at semantics.

    state.position = outcome.position


def run_replay(
    candles: Sequence[Candle],
    *,
    instrument: Instrument,
    timeframe: str,
    exchange_code: str,
    rules: dict,
    initial_equity: Decimal,
    spread: Decimal = Decimal(0),
    signal_generator: BacktestSignalGenerator = generate_dummy_signal,
) -> ReplayResult:
    """Replay `candles` (ascending by `open_time`, final bars only -- the caller is
    responsible for that, matching `_recent_final_candles`'s live-path filter) bar by
    bar. At bar `i`, `signal_generator` only ever sees a bounded window ending at
    `candles[i]` (see `_HISTORY_WINDOW` below) -- no code path in this function reads
    `candles[j]` for `j > i` while evaluating bar `i`, so the look-ahead-bias
    guarantee still holds; it just no longer hands out the full, ever-growing prefix
    to get there."""
    if not candles:
        return ReplayResult(
            trades=[], ending_equity=initial_equity, ending_position=None, equity_curve=[]
        )

    allow_short = exchange_code == "oanda"
    bar_seconds = TIMEFRAME_SECONDS[timeframe]
    state = _ReplayState(cash_equity=initial_equity)

    for i, candle in enumerate(candles):
        now = candle.close_time
        history = candles[max(0, i + 1 - _HISTORY_WINDOW) : i + 1]

        mark_to_market_equity = state.cash_equity + _unrealized_pnl(state.position, candle.close)
        state.equity_curve.append((now, mark_to_market_equity))
        state.peak_equity = max(state.peak_equity, mark_to_market_equity)

        today = now.date()
        if state.day_start_date != today:
            state.day_start_date = today
            state.day_start_equity = mark_to_market_equity
        week_start_day = today - timedelta(days=today.weekday())
        if state.week_start_date != week_start_day:
            state.week_start_date = week_start_day
            state.week_start_equity = mark_to_market_equity

        signal_action = signal_generator(history)

        existing = state.position
        opposing = existing is not None and (
            (signal_action == "buy" and existing.side == "short")
            or (signal_action == "sell" and existing.side == "long")
        )

        if opposing:
            assert existing is not None
            assert signal_action in ("buy", "sell")
            close_fill = fill_sim.simulate_fill(
                exchange_code=exchange_code,
                side=signal_action,
                candle_close=candle.close,
                quantity=existing.quantity,
                expected_slippage=_expected_slippage(exchange_code, spread),
            )
            _apply_and_record(
                state, existing, close_fill, signal_action, now, allow_short=allow_short
            )
            continue

        if signal_action == "hold":
            continue

        expected_slippage = _expected_slippage(exchange_code, spread)
        atr_window = list(history[-risk_gate._ATR_HISTORY_CANDLES :])
        stop_distance = risk_gate._stop_distance(
            exchange_code, instrument, atr_window, spread, rules
        )
        fee_buffer_per_unit = risk_gate._fee_buffer_per_unit(exchange_code, candle.close, rules)
        existing_open_risk = max(Decimal(0), -_unrealized_pnl(state.position, candle.close))
        minutes_since_last_order = (
            Decimal((now - state.last_order_time).total_seconds()) / Decimal(60)
            if state.last_order_time is not None
            else None
        )

        risk_state = risk_gate.RiskState(
            signal_action=signal_action,
            now=now,
            rules=rules,
            exchange_code=exchange_code,
            instrument=instrument,
            market_price=candle.close,
            latest_candle_close_time=candle.close_time,
            bar_seconds=bar_seconds,
            stop_distance=stop_distance,
            expected_slippage=expected_slippage,
            fee_buffer_per_unit=fee_buffer_per_unit,
            equity=mark_to_market_equity,
            existing_open_risk=existing_open_risk,
            has_open_position=state.position is not None,
            existing_position_quantity=(
                state.position.quantity if state.position is not None else Decimal(0)
            ),
            day_start_equity=state.day_start_equity,
            week_start_equity=state.week_start_equity,
            peak_equity=state.peak_equity,
            consecutive_losses=state.consecutive_losses,
            minutes_since_last_order=minutes_since_last_order,
        )
        result = risk_gate._evaluate_conservative_v1(risk_state)
        if result.outcome == "deny":
            continue

        assert result.approved_quantity is not None
        entry_fill = fill_sim.simulate_fill(
            exchange_code=exchange_code,
            side=signal_action,
            candle_close=candle.close,
            quantity=result.approved_quantity,
            expected_slippage=expected_slippage,
        )
        _apply_and_record(
            state, state.position, entry_fill, signal_action, now, allow_short=allow_short
        )
        state.last_order_time = now

    ending_equity = state.cash_equity + _unrealized_pnl(state.position, candles[-1].close)
    return ReplayResult(
        trades=state.trades,
        ending_equity=ending_equity,
        ending_position=state.position,
        equity_curve=state.equity_curve,
    )
