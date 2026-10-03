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
`bot_evaluation.evaluate_bot_on_latest_bar`'s "Dote-gating" behavior, so a backtest
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
into `RiskState` is `cash_equity` plus the signed *market value* of the currently
open position marked at that bar's close, matching `compute_equity`'s own definition
(see that module's docstring for why market value, not unrealized P&L: fixed
2026-09-30, the old `cash + unrealized` omitted the entry notional cash had already
paid out or received).

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
ExitReason = Literal["signal", "stop_loss", "take_profit"]
ExitPolicy = Literal["signal", "stop_loss", "stop_and_target"]
"""How a position may exit besides an opposing signal: `signal` (the signal
only), `stop_loss` (plus a fixed stop, letting winners run until the signal
exits), or `stop_and_target` (plus a fixed take-profit as well)."""

_HISTORY_WINDOW = risk_gate._ATR_HISTORY_CANDLES
"""Bound on how much of `candles` `run_replay` hands a bar's `signal_generator`
(and, downstream, the ATR calculation) -- /code-review finding: `history =
candles[:i+1]` used to copy a growing, unbounded prefix on every one of an N-bar
run's iterations (O(N^2) total). Matches `risk_gate._ATR_HISTORY_CANDLES` (see
that constant's own docstring for its current value and history -- raised
2026-09-28 from 100 to 260 for `ema_trend_signal.py`'s EMA(200) filter). A
custom `signal_generator` that needs a longer lookback than this is not
supported by `run_replay` today -- raise `risk_gate._ATR_HISTORY_CANDLES`
itself (not just a call-site slice here) if one is added, since the ATR window
below relies on the same bound."""


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
    exit_reason: ExitReason = "signal"


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
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    open_entry_fees: Decimal = Decimal(0)
    """Entry fees paid for the currently open position and not yet attributed to
    a `TradeRecord` -- charged to trades in proportion to the quantity they close."""
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    next_sequence_no: int = 1


def _market_value(position: fill_sim.BacktestPosition | None, price: Decimal) -> Decimal:
    if position is None:
        return Decimal(0)
    direction = Decimal(1) if position.side == "long" else Decimal(-1)
    return price * position.quantity * direction


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
    exit_reason: ExitReason = "signal",
) -> None:
    outcome = fill_sim.apply_fill_to_position(
        pre_fill_position, fill, order_side, allow_short=allow_short
    )
    notional = fill.price * fill.quantity
    state.cash_equity += (notional if order_side == "sell" else -notional) - fill.fee_amount

    # Decided by direction, not by `realized_pnl != 0`: a close at exactly the entry
    # price realizes 0 but is still a round trip that paid two fees.
    closes_position = pre_fill_position is not None and order_side == (
        "sell" if pre_fill_position.side == "long" else "buy"
    )
    closing_quantity = (
        min(fill.quantity, pre_fill_position.quantity)
        if closes_position and pre_fill_position is not None
        else Decimal(0)
    )
    closing_fee = (
        fill.fee_amount * closing_quantity / fill.quantity if fill.quantity else Decimal(0)
    )

    if closes_position:
        assert pre_fill_position is not None
        assert state.position_opened_at is not None
        entry_fee_share = state.open_entry_fees * closing_quantity / pre_fill_position.quantity
        state.open_entry_fees -= entry_fee_share
        state.trades.append(
            TradeRecord(
                sequence_no=state.next_sequence_no,
                side=order_side,
                entry_time=state.position_opened_at,
                exit_time=now,
                entry_price=pre_fill_position.average_entry_price,
                exit_price=fill.price,
                quantity=closing_quantity,
                fees=closing_fee + entry_fee_share,
                realized_pnl=outcome.realized_pnl,
                exit_reason=exit_reason,
            )
        )
        state.next_sequence_no += 1
        state.consecutive_losses = state.consecutive_losses + 1 if outcome.realized_pnl < 0 else 0

    state.open_entry_fees += fill.fee_amount - closing_fee

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
        state.stop_price = None
        state.take_profit_price = None
        state.open_entry_fees = Decimal(0)
    # else: same-direction increase or partial reduce -- opened_at unchanged,
    # matching order_flow._apply_fill_to_position's own opened_at semantics.

    state.position = outcome.position


def _protective_exit(
    candle: Candle, position: fill_sim.BacktestPosition, stop: Decimal, target: Decimal | None
) -> tuple[Decimal, ExitReason] | None:
    """Where, if anywhere, `candle` hit `position`'s stop or target. A bar that
    opens beyond a level fills at its open (a gap cannot fill at a price that
    never traded). A bar whose range covers both levels is assumed to have hit
    the stop first: OHLC cannot tell which came first, and assuming the loss is
    the conservative choice."""
    if position.side == "long":
        if candle.open <= stop:
            return candle.open, "stop_loss"
        if candle.low <= stop:
            return stop, "stop_loss"
        if target is None:
            return None
        if candle.open >= target:
            return candle.open, "take_profit"
        if candle.high >= target:
            return target, "take_profit"
        return None
    if candle.open >= stop:
        return candle.open, "stop_loss"
    if candle.high >= stop:
        return stop, "stop_loss"
    if target is None:
        return None
    if candle.open <= target:
        return candle.open, "take_profit"
    if candle.low <= target:
        return target, "take_profit"
    return None


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
    warmup_bars: int = 0,
    exit_policy: ExitPolicy = "signal",
) -> ReplayResult:
    """Replay `candles` (ascending by `open_time`, final bars only -- the caller is
    responsible for that, matching `_recent_final_candles`'s live-path filter) bar by
    bar. At bar `i`, `signal_generator` only ever sees a bounded window ending at
    `candles[i]` (see `_HISTORY_WINDOW` below) -- no code path in this function reads
    `candles[j]` for `j > i` while evaluating bar `i`, so the look-ahead-bias
    guarantee still holds; it just no longer hands out the full, ever-growing prefix
    to get there.

    The first `warmup_bars` candles are history only: they appear in later bars'
    `history` (and ATR window) but are never evaluated, traded, or added to
    `equity_curve`. This lets a caller replaying a sub-window of a longer series
    (rolling walk-forward) hand a long-lookback `signal_generator` such as EMA(200)
    the bars just before the window, instead of losing the window's first ~200 bars
    to an indicator that cannot be computed yet.

    With `exit_policy` other than `signal`, a position opened from flat (or
    flipped) gets a stop at `stop_distance` from its fill price -- the same
    distance the Risk Gate sized it for -- and, with `stop_and_target`, a
    target at `stop_distance * min_reward_risk`. From the next bar on, each
    bar's high/low is checked against them before the signal is evaluated
    (see `_protective_exit`). Adding to a position keeps the original levels.
    The default `signal` keeps existing callers (the backtest API) on the
    signal-only exit behaviour."""
    if warmup_bars < 0:
        raise ValueError("warmup_bars must be non-negative")
    if len(candles) <= warmup_bars:
        return ReplayResult(
            trades=[], ending_equity=initial_equity, ending_position=None, equity_curve=[]
        )

    allow_short = exchange_code == "oanda"
    bar_seconds = TIMEFRAME_SECONDS[timeframe]
    state = _ReplayState(cash_equity=initial_equity)

    for i in range(warmup_bars, len(candles)):
        candle = candles[i]
        now = candle.close_time
        history = candles[max(0, i + 1 - _HISTORY_WINDOW) : i + 1]

        if exit_policy != "signal" and state.position is not None and state.stop_price is not None:
            hit = _protective_exit(
                candle, state.position, state.stop_price, state.take_profit_price
            )
            if hit is not None:
                exit_price, reason = hit
                held = state.position
                exit_side: fill_sim.OrderSide = "sell" if held.side == "long" else "buy"
                exit_fill = fill_sim.simulate_fill(
                    exchange_code=exchange_code,
                    side=exit_side,
                    candle_close=exit_price,
                    quantity=held.quantity,
                    expected_slippage=_expected_slippage(exchange_code, spread),
                )
                _apply_and_record(
                    state, held, exit_fill, exit_side, now,
                    allow_short=allow_short, exit_reason=reason,
                )  # fmt: skip

        mark_to_market_equity = state.cash_equity + _market_value(state.position, candle.close)
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
            available_cash=state.cash_equity,
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
        pre_entry_position = state.position
        _apply_and_record(
            state, pre_entry_position, entry_fill, signal_action, now, allow_short=allow_short
        )
        state.last_order_time = now
        opened_new_position = state.position is not None and (
            pre_entry_position is None or pre_entry_position.side != state.position.side
        )
        if exit_policy != "signal" and opened_new_position:
            direction = Decimal(1) if signal_action == "buy" else Decimal(-1)
            state.stop_price = entry_fill.price - direction * stop_distance
            if exit_policy == "stop_and_target":
                reward_risk = risk_gate._decimal(rules, "min_reward_risk")
                state.take_profit_price = entry_fill.price + direction * stop_distance * reward_risk

    ending_equity = state.cash_equity + _market_value(state.position, candles[-1].close)
    return ReplayResult(
        trades=state.trades,
        ending_equity=ending_equity,
        ending_position=state.position,
        equity_curve=state.equity_curve,
    )
