"""Risk Gate: applies conservative-v1's approved numeric limits
(docs/concept/FXtrading_rebuild/08_取引アルゴリズムとリスク初期値.md§4/§5) to a Signal,
producing a `risk_decision` row and, when approved, a quantity to trade.

**Scope decision (reported, not decided silently -- see the implementation plan and
completion report): both the "初期値" (initial) and "system hard limit" columns of §4
are applied as *deny* thresholds for an individual order here.** Treating "初期値" as
inert until a future halt-state task exists would mean conservative-v1's own numbers
are not actually enforced yet, which defeats this task's purpose; treating only "hard
limit" as binding would silently loosen the approved profile. Both are therefore
enforced as DB-level `risk_decision.outcome="deny"` per order.

**`trading_halt` (updated, ADR 0004/docs/plans/trading-halt-mvp.md)**: this module
used to leave `trading_halt` activation/release entirely out of scope. It no longer
does, for exactly two causes: `evaluate_signal` now calls
`app.trading.application.trading_halt` to activate/escalate on a data-delay or
daily/weekly-loss/peak-drawdown hard breach, and to relax one step when the
corresponding check passes again -- see `_sync_trading_halts` below. The other 8
causes in 05番's 取引停止マトリクス still have no detection code anywhere and remain
out of scope (ADR 0004's explicit MVP boundary).

**Approximation (reported): "全open positionの想定損失合計"** would need each open
position's stop-loss-implied risk, but this codebase does not place or track real
stop-loss orders (`trade_order.stop_price` exists but nothing acts on it). This check
therefore uses current *unrealized loss* (0 if the open position is not currently
underwater) as a stand-in for "loss if stopped out" -- documented here and in
`rule_results` rather than silently treated as exact.

**Fee units note**: `order_flow._fee_buffer` returns a *total* monetary fee for a given
quantity (`price * quantity * rate`), meant for a fill already at a known quantity.
08_取引アルゴリズムとリスク初期値.md§5's sizing formula instead needs a *per-unit*
fee distance to add to `stop_distance` (itself a price distance) before quantity is
even known -- reusing `_fee_buffer` here would be a unit mismatch (and, because it's
linear in quantity, circular: quantity depends on stress_adjusted_stop which would
depend on quantity). Since the fee is a flat percentage of notional, the per-unit fee
is simply `price * rate`, independent of quantity; `_fee_buffer_per_unit` below
computes that instead of calling `order_flow._fee_buffer`.

Not implemented (found, not fabricated -- see completion report): OANDA's
`marginCallPercent`/`marginCloseoutPercent` checks from §4 require live margin-usage
data from the broker; this paper simulator has no such data source (paper accounts
never call OANDA's account endpoints), so these two checks are omitted rather than
computed from a fabricated value.

**Unit 2 refactor (docs/plans/horizon4-lite-backtest.md)**: `evaluate_signal`'s body
used to interleave DB reads with conservative-v1's rule logic in one function. It is
now split into two phases: this module still does all the DB reads (unchanged
queries, unchanged order, unchanged `RiskDecision` persistence), but everything it
learns from the DB is now collected into a `RiskState` value first, and every
conservative-v1 threshold check now lives in `_evaluate_conservative_v1`, a function
that takes only a `RiskState` and returns a `PureRiskResult` -- no `Session`, no
`datetime.now()`, no other DB-shaped object. `evaluate_signal`'s own signature,
external behavior, and the sequence/count of queries it issues are unchanged (see
the test suite's `side_effect` lists, which assert query count for the equity<=0
path). The point of the split: `_evaluate_conservative_v1` can be called with a
backtest replay's *simulated* state just as easily as with the live DB's current
state, without duplicating conservative-v1's logic -- the eventual backtest harness
(Unit 3/4) builds its own `RiskState` instead of adding a second implementation of
this function.

**One deliberate, reported behavior change**: the old code called `compute_equity`
twice for a Binance signal -- once into `equity`, and again into a same-valued
`available_jpy` inside the Binance `broker_limit` branch (nothing mutates the
account between the two calls within one `evaluate_signal` invocation, so they were
always equal). `_evaluate_conservative_v1` now reuses `state.equity` for that
calculation instead, removing the redundant second query. Every other query this
module issues is unchanged in both count and order.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.application.indicators import average_true_range
from app.models.instruments import Instrument
from app.models.market_data import Candle, InstrumentSpread
from app.models.strategy import BotRun, RiskDecision, RiskProfileVersion, Signal, TradingBot
from app.models.trading import (
    AccountSnapshot,
    LedgerEntry,
    OrderIntent,
    TradeOrder,
    TradingAccount,
    TradingPosition,
)
from app.trading.application import order_flow, trading_halt
from app.trading.application.account_valuation import compute_equity

CONSERVATIVE_V1_RULES: dict[str, object] = {
    "risk_per_trade": "0.005",
    "risk_per_trade_hard": "0.02",
    "all_open_risk_limit": "0.015",
    "all_open_risk_limit_hard": "0.05",
    "daily_loss_limit": "0.02",
    "daily_loss_limit_hard": "0.05",
    "weekly_loss_limit": "0.04",
    "weekly_loss_limit_hard": "0.08",
    "peak_drawdown_limit": "0.05",
    "peak_drawdown_limit_hard": "0.10",
    "consecutive_loss_limit": 3,
    "consecutive_loss_limit_hard": 7,
    "min_reward_risk": "2.0",
    "min_reward_risk_hard": "1.0",
    "min_order_interval_minutes": 15,
    "min_order_interval_minutes_hard": 1,
    "max_data_delay_bars": 2,
    "max_data_delay_bars_hard": 5,
    "oanda": {
        "leverage_cap": "3.0",
        "stop_atr_multiplier": "1.5",
        "stop_spread_multiplier": "3.0",
    },
    "binance": {
        "stop_atr_multiplier": "2.0",
        "stop_spread_multiplier": "3.0",
        "fee_rate": "0.001",
        "order_limit_pct_of_available": "0.10",
        "btc_holding_cap_pct_of_equity": "0.25",
    },
}
"""08_取引アルゴリズムとリスク初期値.md§4/§5's approved conservative-v1 numbers, as the
JSON payload for the dummy `risk_profile_version.rules` row `dummy_pipeline.py`
creates. Kept as a module-level constant (rather than only living in the DB row) so
tests can assert against it directly without a DB round trip."""


class RiskGateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RiskEvaluationResult:
    decision: RiskDecision
    approved_quantity: Decimal | None
    """None when outcome == "deny"."""


def _decimal(rules: dict, key: str) -> Decimal:
    return Decimal(str(rules[key]))


_ATR_HISTORY_CANDLES = 100
"""How many candles to fetch for `average_true_range`'s ATR(14). Wilder's smoothing
(see indicators.py) only reflects recent volatility once it has enough bars to roll
forward past the initial seed -- fetching just `period + 1` (15) would give the same
result as the old flat-SMA version, defeating the point of switching to Wilder's
method. 100 is a judgment call (not derived from a formula): enough bars that the
seed's influence has decayed by roughly (13/14)^85, without an unbounded query."""


def _recent_final_candles(
    db: Session, instrument_id: UUID, timeframe: str, limit: int
) -> list[Candle]:
    rows = db.scalars(
        select(Candle)
        .where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == timeframe,
            Candle.is_final.is_(True),
        )
        .order_by(Candle.open_time.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))  # ascending, oldest first (what average_true_range expects)


def _fee_buffer_per_unit(exchange_code: str, price: Decimal, rules: dict) -> Decimal:
    """See module docstring's "Fee units note"."""
    if exchange_code == "oanda":
        return Decimal(0)
    if exchange_code == "binance":
        return price * _decimal(rules["binance"], "fee_rate")
    raise RiskGateError("unsupported_exchange", f"No fee rule for exchange '{exchange_code}'")


def _stop_distance(
    exchange_code: str,
    instrument: Instrument,
    candles: list[Candle],
    spread: Decimal,
    rules: dict,
) -> Decimal:
    atr = average_true_range(candles, period=14)
    exchange_rules = rules[exchange_code]
    atr_component = (
        (atr * _decimal(exchange_rules, "stop_atr_multiplier")) if atr is not None else Decimal(0)
    )
    spread_component = spread * _decimal(exchange_rules, "stop_spread_multiplier")
    components = [atr_component, spread_component]
    if exchange_code == "oanda":
        # Placeholder broker minimum distance (unapproved, see module docstring):
        # no confirmed OANDA-published minimum-stop-distance value is wired in yet.
        components.append(instrument.tick_size * Decimal(50))
    return max(components)


def _spread(db: Session, exchange_code: str, instrument_id: UUID) -> Decimal:
    if exchange_code != "oanda":
        return Decimal(0)
    row = db.get(InstrumentSpread, instrument_id)
    if row is None:
        return Decimal(0)
    return row.ask - row.bid


def _open_position(
    db: Session, account: TradingAccount, instrument: Instrument
) -> TradingPosition | None:
    return db.scalar(
        select(TradingPosition).where(
            TradingPosition.account_id == account.id,
            TradingPosition.instrument_id == instrument.id,
            TradingPosition.status == "open",
        )
    )


def _existing_open_risk(db: Session, account: TradingAccount, instrument: Instrument) -> Decimal:
    """Approximation: current unrealized loss magnitude (0 if not underwater). See
    module docstring."""
    position = _open_position(db, account, instrument)
    if position is None or position.average_entry_price is None:
        return Decimal(0)
    candle = db.scalar(
        select(Candle)
        .where(Candle.instrument_id == instrument.id, Candle.is_final.is_(True))
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if candle is None:
        return Decimal(0)
    direction = Decimal(1) if position.side == "long" else Decimal(-1)
    unrealized = (candle.close - position.average_entry_price) * position.quantity * direction
    return -unrealized if unrealized < 0 else Decimal(0)


def _equity_at(db: Session, account: TradingAccount, since: datetime) -> Decimal | None:
    row = db.scalar(
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account.id, AccountSnapshot.captured_at >= since)
        .order_by(AccountSnapshot.captured_at.asc())
        .limit(1)
    )
    return row.equity if row is not None else None


def _peak_equity(db: Session, account: TradingAccount) -> Decimal | None:
    return db.scalar(
        select(func.max(AccountSnapshot.equity)).where(AccountSnapshot.account_id == account.id)
    )


def _consecutive_losses(db: Session, account: TradingAccount) -> int:
    rows = db.scalars(
        select(LedgerEntry.amount)
        .where(LedgerEntry.account_id == account.id, LedgerEntry.entry_type == "realized_pnl")
        .order_by(LedgerEntry.occurred_at.desc())
        .limit(50)
    ).all()
    count = 0
    for amount in rows:
        if amount < 0:
            count += 1
        else:
            break
    return count


def _minutes_since_last_order(db: Session, bot_id: UUID, now: datetime) -> Decimal | None:
    last = db.scalar(
        select(TradeOrder.created_at)
        .join(OrderIntent, TradeOrder.order_intent_id == OrderIntent.id)
        .join(Signal, OrderIntent.signal_id == Signal.id)
        .join(BotRun, Signal.bot_run_id == BotRun.id)
        .where(BotRun.bot_id == bot_id)
        .order_by(TradeOrder.created_at.desc())
        .limit(1)
    )
    if last is None:
        return None
    delta = now - last
    return Decimal(delta.total_seconds()) / Decimal(60)


@dataclass(frozen=True)
class RiskState:
    """Every DB-/clock-derived input conservative-v1's threshold checks need,
    already resolved to plain values -- gathering these (the only "impure" part of
    risk evaluation) is `evaluate_signal`'s job; `_evaluate_conservative_v1` below
    is a pure function of a `RiskState`. `instrument` is included as-is (not
    decomposed into its individual fields) since it is static reference data, not
    time-varying account/market state -- passing it whole matches how
    `_stop_distance` already takes it."""

    signal_action: str
    now: datetime
    rules: dict
    exchange_code: str
    instrument: Instrument
    market_price: Decimal
    latest_candle_close_time: datetime
    bar_seconds: int
    stop_distance: Decimal
    expected_slippage: Decimal
    fee_buffer_per_unit: Decimal
    equity: Decimal
    existing_open_risk: Decimal
    has_open_position: bool
    existing_position_quantity: Decimal
    """0 unless there is an open position on this instrument (Binance-only in
    practice -- see `evaluate_signal`'s Binance-only fetch below). /code-review
    finding: the `binance_btc_holding_cap` check used to compare only the
    *new* order's notional against the cap, so an existing 20%-of-equity BTC
    position plus a fresh 10% buy (each individually within the 25% cap) could
    push total holdings to 30% without either check ever seeing the other."""
    day_start_equity: Decimal | None
    week_start_equity: Decimal | None
    peak_equity: Decimal | None
    consecutive_losses: int
    minutes_since_last_order: Decimal | None


@dataclass(frozen=True)
class PureRiskResult:
    rule_results: dict[str, object]
    outcome: str
    approved_quantity: Decimal | None
    reason_code: str | None


def _evaluate_conservative_v1(state: RiskState) -> PureRiskResult:
    """conservative-v1's threshold checks (08_取引アルゴリズムとリスク初期値.md§4/§5),
    given a `RiskState`. Caller (`evaluate_signal`) is responsible for the
    `state.equity <= 0` short-circuit -- see its own docstring/comment for why that
    case stays there instead of here."""
    rules = state.rules
    instrument = state.instrument
    results: dict[str, object] = {"equity": {"passed": True, "value": str(state.equity)}}
    hard_breach = False
    initial_breach = False

    stress_adjusted_stop = state.stop_distance + state.expected_slippage + state.fee_buffer_per_unit

    risk_budget = state.equity * _decimal(rules, "risk_per_trade")
    raw_quantity = (risk_budget / stress_adjusted_stop) if stress_adjusted_stop > 0 else Decimal(0)

    # --- exposure_limit: remaining room under the "all open positions" risk cap ---
    all_open_limit_amount = state.equity * _decimal(rules, "all_open_risk_limit")
    remaining_risk_budget = all_open_limit_amount - state.existing_open_risk
    exposure_limit_quantity = (
        (remaining_risk_budget / stress_adjusted_stop)
        if remaining_risk_budget > 0 and stress_adjusted_stop > 0
        else Decimal(0)
    )

    # --- broker_limit ---
    broker_limit_quantity = instrument.max_quantity if instrument.max_quantity is not None else None
    if state.exchange_code == "binance":
        order_limit_notional = state.equity * _decimal(
            rules["binance"], "order_limit_pct_of_available"
        )
        binance_order_limit_quantity = (
            order_limit_notional / state.market_price if state.market_price > 0 else Decimal(0)
        )
        broker_limit_quantity = (
            binance_order_limit_quantity
            if broker_limit_quantity is None
            else min(broker_limit_quantity, binance_order_limit_quantity)
        )
    if state.exchange_code == "oanda":
        leverage_cap = _decimal(rules["oanda"], "leverage_cap")
        leverage_limit_quantity = (
            (state.equity * leverage_cap / state.market_price)
            if state.market_price > 0
            else Decimal(0)
        )
        broker_limit_quantity = (
            leverage_limit_quantity
            if broker_limit_quantity is None
            else min(broker_limit_quantity, leverage_limit_quantity)
        )

    candidates = [raw_quantity, exposure_limit_quantity]
    if broker_limit_quantity is not None:
        candidates.append(broker_limit_quantity)
    quantity = min(candidates)
    if quantity < 0:
        quantity = Decimal(0)
    step_size = instrument.step_size
    if step_size > 0:
        steps = (quantity / step_size).to_integral_value(rounding=ROUND_FLOOR)
        quantity = steps * step_size
    was_adjusted = quantity < raw_quantity

    quantity_too_small = quantity <= 0 or (
        instrument.min_quantity is not None and quantity < instrument.min_quantity
    )
    results["quantity_calculation"] = {
        "raw_quantity": str(raw_quantity),
        "exposure_limit_quantity": str(exposure_limit_quantity),
        "broker_limit_quantity": (
            str(broker_limit_quantity) if broker_limit_quantity is not None else None
        ),
        "final_quantity": str(quantity),
        "stop_distance": str(state.stop_distance),
        "expected_slippage": str(state.expected_slippage),
        "fee_buffer_per_unit": str(state.fee_buffer_per_unit),
        "passed": not quantity_too_small,
    }
    if quantity_too_small:
        hard_breach = True

    # --- per-trade loss (self-consistent by construction; verified defensively) ---
    loss_at_stop = quantity * stress_adjusted_stop
    loss_pct = (loss_at_stop / state.equity) if state.equity > 0 else Decimal(1)
    results["per_trade_loss"] = {
        "passed": loss_pct <= _decimal(rules, "risk_per_trade_hard"),
        "pct": str(loss_pct),
    }
    if loss_pct > _decimal(rules, "risk_per_trade_hard"):
        hard_breach = True
    elif loss_pct > _decimal(rules, "risk_per_trade"):
        initial_breach = True

    # --- all-open risk ---
    all_open_pct = (
        ((state.existing_open_risk + loss_at_stop) / state.equity)
        if state.equity > 0
        else Decimal(1)
    )
    results["all_open_risk"] = {
        "passed": all_open_pct <= _decimal(rules, "all_open_risk_limit_hard"),
        "pct": str(all_open_pct),
        "approximation": "uses current unrealized loss, not real stop-loss tracking",
    }
    if all_open_pct > _decimal(rules, "all_open_risk_limit_hard"):
        hard_breach = True
    elif all_open_pct > _decimal(rules, "all_open_risk_limit"):
        initial_breach = True

    # --- daily / weekly loss ---
    for label, window_equity, limit_key, limit_key_hard in (
        ("daily_loss", state.day_start_equity, "daily_loss_limit", "daily_loss_limit_hard"),
        ("weekly_loss", state.week_start_equity, "weekly_loss_limit", "weekly_loss_limit_hard"),
    ):
        if window_equity is None or window_equity <= 0:
            results[label] = {"passed": True, "note": "no snapshot yet in this window"}
            continue
        loss_pct_window = max(Decimal(0), (window_equity - state.equity) / window_equity)
        results[label] = {
            "passed": loss_pct_window <= _decimal(rules, limit_key_hard),
            "pct": str(loss_pct_window),
        }
        if loss_pct_window > _decimal(rules, limit_key_hard):
            hard_breach = True
        elif loss_pct_window > _decimal(rules, limit_key):
            initial_breach = True

    # --- peak drawdown ---
    if state.peak_equity is None or state.peak_equity <= 0:
        results["peak_drawdown"] = {"passed": True, "note": "no snapshot history yet"}
    else:
        dd_pct = max(Decimal(0), (state.peak_equity - state.equity) / state.peak_equity)
        results["peak_drawdown"] = {
            "passed": dd_pct <= _decimal(rules, "peak_drawdown_limit_hard"),
            "pct": str(dd_pct),
        }
        if dd_pct > _decimal(rules, "peak_drawdown_limit_hard"):
            hard_breach = True
        elif dd_pct > _decimal(rules, "peak_drawdown_limit"):
            initial_breach = True

    # --- consecutive losses ---
    results["consecutive_losses"] = {
        "passed": state.consecutive_losses <= int(rules["consecutive_loss_limit_hard"]),
        "count": state.consecutive_losses,
    }
    if state.consecutive_losses > int(rules["consecutive_loss_limit_hard"]):
        hard_breach = True
    elif state.consecutive_losses > int(rules["consecutive_loss_limit"]):
        initial_breach = True

    # --- reward/risk (see dummy_signal.py: constructed to satisfy this by design) ---
    reward_risk = Decimal("2.0")
    results["reward_risk"] = {
        "passed": reward_risk >= _decimal(rules, "min_reward_risk_hard"),
        "ratio": str(reward_risk),
    }
    if reward_risk < _decimal(rules, "min_reward_risk_hard"):
        hard_breach = True
    elif reward_risk < _decimal(rules, "min_reward_risk"):
        initial_breach = True

    # --- order interval ---
    if state.minutes_since_last_order is None:
        results["order_interval"] = {"passed": True, "note": "no prior order for this bot"}
    else:
        results["order_interval"] = {
            "passed": state.minutes_since_last_order
            >= Decimal(rules["min_order_interval_minutes_hard"]),
            "minutes_since_last_order": str(state.minutes_since_last_order),
        }
        if state.minutes_since_last_order < Decimal(rules["min_order_interval_minutes_hard"]):
            hard_breach = True
        elif state.minutes_since_last_order < Decimal(rules["min_order_interval_minutes"]):
            initial_breach = True

    # --- data delay ---
    delay_seconds = (state.now - state.latest_candle_close_time).total_seconds()
    delay_bars = Decimal(delay_seconds) / Decimal(state.bar_seconds)
    results["data_delay"] = {
        "passed": delay_bars <= Decimal(rules["max_data_delay_bars_hard"]),
        "bars": str(delay_bars),
    }
    if delay_bars > Decimal(rules["max_data_delay_bars_hard"]):
        hard_breach = True
    elif delay_bars > Decimal(rules["max_data_delay_bars"]):
        initial_breach = True

    # --- Binance-specific re-checks (defense in depth; order_flow.py also enforces
    #     no-short structurally) ---
    if state.exchange_code == "binance":
        if state.signal_action == "sell" and not state.has_open_position:
            results["binance_no_short"] = {"passed": False}
            hard_breach = True
        else:
            results["binance_no_short"] = {"passed": True}
        btc_cap = state.equity * _decimal(rules["binance"], "btc_holding_cap_pct_of_equity")
        # /code-review finding: combine with the position already held, not just
        # this order's own quantity -- a buy grows the holding, a sell shrinks it.
        if state.signal_action == "buy":
            prospective_position_quantity = state.existing_position_quantity + quantity
        else:
            prospective_position_quantity = max(
                Decimal(0), state.existing_position_quantity - quantity
            )
        prospective_notional = prospective_position_quantity * state.market_price
        results["binance_btc_holding_cap"] = {
            "passed": prospective_notional <= btc_cap,
            "existing_position_quantity": str(state.existing_position_quantity),
            "prospective_notional": str(prospective_notional),
            "cap": str(btc_cap),
        }
        if prospective_notional > btc_cap:
            hard_breach = True

    if hard_breach or initial_breach:
        outcome = "deny"
        approved_quantity: Decimal | None = None
        reason_code = "hard_limit_breach" if hard_breach else "initial_threshold_breach"
    elif was_adjusted:
        outcome = "allow_with_adjustment"
        approved_quantity = quantity
        reason_code = None
    else:
        outcome = "allow"
        approved_quantity = quantity
        reason_code = None

    return PureRiskResult(
        rule_results=results,
        outcome=outcome,
        approved_quantity=approved_quantity,
        reason_code=reason_code,
    )


def evaluate_signal(
    db: Session,
    signal: Signal,
    account: TradingAccount,
    instrument: Instrument,
    bot: TradingBot,
    risk_profile_version: RiskProfileVersion,
    exchange_code: str,
) -> RiskEvaluationResult:
    """Evaluate one `buy`/`sell` Signal against conservative-v1 and persist the
    resulting `risk_decision`. Caller (`dummy_pipeline.py`) is responsible for
    deciding *whether* to call this at all (e.g. `hold` signals, or a signal that
    dote-gating says must be close-only, never reach this function).

    This function's job is now only to gather DB/clock state (unchanged queries,
    unchanged order) and persist the resulting `RiskDecision`; the conservative-v1
    threshold logic itself lives in `_evaluate_conservative_v1`. See the module
    docstring's "Unit 2 refactor" note."""
    if signal.action not in ("buy", "sell"):
        raise RiskGateError(
            "invalid_signal_action", f"Cannot risk-evaluate a '{signal.action}' signal"
        )

    rules = risk_profile_version.rules
    now = datetime.now(UTC)

    candles = _recent_final_candles(db, instrument.id, bot.timeframe, _ATR_HISTORY_CANDLES)
    latest_candle = candles[-1] if candles else None
    if latest_candle is None:
        raise RiskGateError(
            "market_price_unavailable", "No final candle available for this instrument"
        )
    market_price = latest_candle.close

    spread = _spread(db, exchange_code, instrument.id)
    stop_distance = _stop_distance(exchange_code, instrument, candles, spread, rules)
    # /code-review finding: use order_flow.py's shared coefficient table instead of
    # a second hardcoded copy of the same two numbers (see its docstring).
    expected_slippage = spread * order_flow.SLIPPAGE_COEFFICIENT_BY_EXCHANGE.get(
        exchange_code, Decimal(0)
    )
    fee_buffer = _fee_buffer_per_unit(exchange_code, market_price, rules)

    equity = compute_equity(db, account, instrument)
    if equity <= 0:
        # Short-circuit, kept here (not in `_evaluate_conservative_v1`): every
        # downstream check divides by or scales with equity, so there is nothing
        # meaningful left to compute once it's non-positive, and -- the reason this
        # stays a DB-layer concern -- no further queries are needed to know the
        # answer is deny (see the test suite's `side_effect` list for this path).
        decision = RiskDecision(
            signal_id=signal.id,
            risk_profile_version_id=risk_profile_version.id,
            outcome="deny",
            rule_results={"equity": {"passed": False, "value": str(equity)}},
            adjusted_quantity=None,
            reason_code="hard_limit_breach",
        )
        db.add(decision)
        db.flush()
        db.refresh(decision)
        return RiskEvaluationResult(decision=decision, approved_quantity=None)

    existing_open_risk = _existing_open_risk(db, account, instrument)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=now.weekday())
    day_start_equity = _equity_at(db, account, day_start)
    week_start_equity = _equity_at(db, account, week_start)
    peak_equity = _peak_equity(db, account)
    consecutive_losses = _consecutive_losses(db, account)
    minutes_since_last_order = _minutes_since_last_order(db, bot.id, now)
    # Matches the original code's query count exactly: `_open_position` was only
    # ever queried a second time (on top of `_existing_open_risk`'s own internal
    # call) for Binance's no-short re-check, so only fetch it here for Binance too.
    # Also used for `existing_position_quantity` below (/code-review finding on
    # the BTC holding cap) -- same row, no extra query for that.
    binance_position = (
        _open_position(db, account, instrument) if exchange_code == "binance" else None
    )
    has_open_position = binance_position is not None
    existing_position_quantity = (
        binance_position.quantity if binance_position is not None else Decimal(0)
    )

    state = RiskState(
        signal_action=signal.action,
        now=now,
        rules=rules,
        exchange_code=exchange_code,
        instrument=instrument,
        market_price=market_price,
        latest_candle_close_time=latest_candle.close_time,
        bar_seconds=TIMEFRAME_SECONDS[bot.timeframe],
        stop_distance=stop_distance,
        expected_slippage=expected_slippage,
        fee_buffer_per_unit=fee_buffer,
        equity=equity,
        existing_open_risk=existing_open_risk,
        has_open_position=has_open_position,
        existing_position_quantity=existing_position_quantity,
        day_start_equity=day_start_equity,
        week_start_equity=week_start_equity,
        peak_equity=peak_equity,
        consecutive_losses=consecutive_losses,
        minutes_since_last_order=minutes_since_last_order,
    )
    result = _evaluate_conservative_v1(state)

    decision = RiskDecision(
        signal_id=signal.id,
        risk_profile_version_id=risk_profile_version.id,
        outcome=result.outcome,
        rule_results=result.rule_results,
        adjusted_quantity=(
            result.approved_quantity if result.outcome == "allow_with_adjustment" else None
        ),
        reason_code=result.reason_code,
    )
    db.add(decision)
    db.flush()
    db.refresh(decision)
    _sync_trading_halts(db, bot, account, result, now)
    return RiskEvaluationResult(decision=decision, approved_quantity=result.approved_quantity)


def _rule_passed(rule_results: dict[str, object], key: str) -> bool:
    """`PureRiskResult.rule_results` is `dict[str, object]` (each value itself a
    small `{"passed": bool, ...}` dict) -- this narrows one value back to that
    shape for mypy, instead of scattering `# type: ignore[index]` at every call
    site."""
    rule = rule_results[key]
    assert isinstance(rule, dict)
    return bool(rule["passed"])


def _sync_trading_halts(
    db: Session, bot: TradingBot, account: TradingAccount, result: PureRiskResult, now: datetime
) -> None:
    """ADR 0004 / docs/plans/trading-halt-mvp.md Unit B: reflects the two in-scope
    hard-breach checks into `trading_halt` (activate/escalate to `entry_halted` on
    breach, relax one step on recovery). Not called from the `equity <= 0`
    short-circuit path above -- that case has no `result.rule_results` to read (see
    module docstring's "Unit 2 refactor" note) and is a different, more severe
    problem than either of these two causes. Uses `evaluate_signal`'s own `now`
    (not a fresh clock read), matching the rest of this function's single-`now`
    convention."""
    data_delay_scope = trading_halt.HaltScope(
        workspace_id=bot.workspace_id, scope_type="bot", scope_id=bot.id
    )
    if _rule_passed(result.rule_results, "data_delay"):
        trading_halt.deescalate_one_step(db, data_delay_scope, reason_code="data_delay", now=now)
    else:
        trading_halt.activate_or_escalate(
            db, data_delay_scope, reason_code="data_delay", level="entry_halted"
        )

    loss_dd_scope = trading_halt.HaltScope(
        workspace_id=bot.workspace_id, scope_type="account", scope_id=account.id
    )
    loss_dd_ok = (
        _rule_passed(result.rule_results, "daily_loss")
        and _rule_passed(result.rule_results, "weekly_loss")
        and _rule_passed(result.rule_results, "peak_drawdown")
    )
    if loss_dd_ok:
        trading_halt.deescalate_one_step(
            db, loss_dd_scope, reason_code="daily_loss_dd_limit", now=now
        )
    else:
        trading_halt.activate_or_escalate(
            db, loss_dd_scope, reason_code="daily_loss_dd_limit", level="entry_halted"
        )
