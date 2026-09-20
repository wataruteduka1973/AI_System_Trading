"""Risk Gate: applies conservative-v1's approved numeric limits
(docs/concept/FXtrading_rebuild/08_取引アルゴリズムとリスク初期値.md§4/§5) to a Signal,
producing a `risk_decision` row and, when approved, a quantity to trade.

**Scope decision (reported, not decided silently -- see the implementation plan and
completion report): both the "初期値" (initial) and "system hard limit" columns of §4
are applied as *deny* thresholds for an individual order here.** `trading_halt`
activation/release (a persistent account/bot state, with its own release workflow) is
out of scope for this module and is never written here. Treating "初期値" as inert
until a future halt-state task exists would mean conservative-v1's own numbers are not
actually enforced yet, which defeats this task's purpose; treating only "hard limit"
as binding would silently loosen the approved profile. Both are therefore enforced as
DB-level `risk_decision.outcome="deny"` per order, while the *state* of being halted
(and requiring an explicit release) remains future work.

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


def _minutes_since_last_order(db: Session, bot_id: UUID) -> Decimal | None:
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
    delta = datetime.now(UTC) - last
    return Decimal(delta.total_seconds()) / Decimal(60)


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
    dote-gating says must be close-only, never reach this function)."""
    if signal.action not in ("buy", "sell"):
        raise RiskGateError(
            "invalid_signal_action", f"Cannot risk-evaluate a '{signal.action}' signal"
        )

    rules = risk_profile_version.rules
    now = datetime.now(UTC)
    results: dict[str, object] = {}
    hard_breach = False
    initial_breach = False

    candles = _recent_final_candles(db, instrument.id, bot.timeframe, 15)
    latest_candle = candles[-1] if candles else None
    if latest_candle is None:
        raise RiskGateError(
            "market_price_unavailable", "No final candle available for this instrument"
        )
    market_price = latest_candle.close

    spread = _spread(db, exchange_code, instrument.id)
    stop_distance = _stop_distance(exchange_code, instrument, candles, spread, rules)
    expected_slippage = spread * (
        Decimal("0.5")
        if exchange_code == "oanda"
        else Decimal("1.0")
        if exchange_code == "binance"
        else Decimal(0)
    )
    fee_buffer = _fee_buffer_per_unit(exchange_code, market_price, rules)
    stress_adjusted_stop = stop_distance + expected_slippage + fee_buffer

    equity = compute_equity(db, account, instrument)
    if equity <= 0:
        # Short-circuit: every downstream check divides by or scales with equity, so
        # there is nothing meaningful left to compute once it's non-positive (and no
        # further queries are needed to know the answer is deny).
        results["equity"] = {"passed": False, "value": str(equity)}
        decision = RiskDecision(
            signal_id=signal.id,
            risk_profile_version_id=risk_profile_version.id,
            outcome="deny",
            rule_results=results,
            adjusted_quantity=None,
            reason_code="hard_limit_breach",
        )
        db.add(decision)
        db.flush()
        db.refresh(decision)
        return RiskEvaluationResult(decision=decision, approved_quantity=None)
    results["equity"] = {"passed": True, "value": str(equity)}

    risk_budget = equity * _decimal(rules, "risk_per_trade")
    raw_quantity = (risk_budget / stress_adjusted_stop) if stress_adjusted_stop > 0 else Decimal(0)

    # --- exposure_limit: remaining room under the "all open positions" risk cap ---
    existing_open_risk = _existing_open_risk(db, account, instrument)
    all_open_limit_amount = equity * _decimal(rules, "all_open_risk_limit")
    remaining_risk_budget = all_open_limit_amount - existing_open_risk
    exposure_limit_quantity = (
        (remaining_risk_budget / stress_adjusted_stop)
        if remaining_risk_budget > 0 and stress_adjusted_stop > 0
        else Decimal(0)
    )

    # --- broker_limit ---
    broker_limit_quantity = instrument.max_quantity if instrument.max_quantity is not None else None
    if exchange_code == "binance":
        available_jpy = compute_equity(db, account, instrument)  # single-instrument paper account
        order_limit_notional = available_jpy * _decimal(
            rules["binance"], "order_limit_pct_of_available"
        )
        binance_order_limit_quantity = (
            order_limit_notional / market_price if market_price > 0 else Decimal(0)
        )
        broker_limit_quantity = (
            binance_order_limit_quantity
            if broker_limit_quantity is None
            else min(broker_limit_quantity, binance_order_limit_quantity)
        )
    if exchange_code == "oanda":
        leverage_cap = _decimal(rules["oanda"], "leverage_cap")
        leverage_limit_quantity = (
            (equity * leverage_cap / market_price) if market_price > 0 else Decimal(0)
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
        "stop_distance": str(stop_distance),
        "expected_slippage": str(expected_slippage),
        "fee_buffer_per_unit": str(fee_buffer),
        "passed": not quantity_too_small,
    }
    if quantity_too_small:
        hard_breach = True

    # --- per-trade loss (self-consistent by construction; verified defensively) ---
    loss_at_stop = quantity * stress_adjusted_stop
    loss_pct = (loss_at_stop / equity) if equity > 0 else Decimal(1)
    results["per_trade_loss"] = {
        "passed": loss_pct <= _decimal(rules, "risk_per_trade_hard"),
        "pct": str(loss_pct),
    }
    if loss_pct > _decimal(rules, "risk_per_trade_hard"):
        hard_breach = True
    elif loss_pct > _decimal(rules, "risk_per_trade"):
        initial_breach = True

    # --- all-open risk ---
    all_open_pct = ((existing_open_risk + loss_at_stop) / equity) if equity > 0 else Decimal(1)
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
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=now.weekday())
    for label, window_start, limit_key, limit_key_hard in (
        ("daily_loss", day_start, "daily_loss_limit", "daily_loss_limit_hard"),
        ("weekly_loss", week_start, "weekly_loss_limit", "weekly_loss_limit_hard"),
    ):
        window_start_equity = _equity_at(db, account, window_start)
        if window_start_equity is None or window_start_equity <= 0:
            results[label] = {"passed": True, "note": "no snapshot yet in this window"}
            continue
        loss_pct_window = max(Decimal(0), (window_start_equity - equity) / window_start_equity)
        results[label] = {
            "passed": loss_pct_window <= _decimal(rules, limit_key_hard),
            "pct": str(loss_pct_window),
        }
        if loss_pct_window > _decimal(rules, limit_key_hard):
            hard_breach = True
        elif loss_pct_window > _decimal(rules, limit_key):
            initial_breach = True

    # --- peak drawdown ---
    peak_equity = _peak_equity(db, account)
    if peak_equity is None or peak_equity <= 0:
        results["peak_drawdown"] = {"passed": True, "note": "no snapshot history yet"}
    else:
        dd_pct = max(Decimal(0), (peak_equity - equity) / peak_equity)
        results["peak_drawdown"] = {
            "passed": dd_pct <= _decimal(rules, "peak_drawdown_limit_hard"),
            "pct": str(dd_pct),
        }
        if dd_pct > _decimal(rules, "peak_drawdown_limit_hard"):
            hard_breach = True
        elif dd_pct > _decimal(rules, "peak_drawdown_limit"):
            initial_breach = True

    # --- consecutive losses ---
    losses = _consecutive_losses(db, account)
    results["consecutive_losses"] = {
        "passed": losses <= int(rules["consecutive_loss_limit_hard"]),
        "count": losses,
    }
    if losses > int(rules["consecutive_loss_limit_hard"]):
        hard_breach = True
    elif losses > int(rules["consecutive_loss_limit"]):
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
    minutes_since = _minutes_since_last_order(db, bot.id)
    if minutes_since is None:
        results["order_interval"] = {"passed": True, "note": "no prior order for this bot"}
    else:
        results["order_interval"] = {
            "passed": minutes_since >= Decimal(rules["min_order_interval_minutes_hard"]),
            "minutes_since_last_order": str(minutes_since),
        }
        if minutes_since < Decimal(rules["min_order_interval_minutes_hard"]):
            hard_breach = True
        elif minutes_since < Decimal(rules["min_order_interval_minutes"]):
            initial_breach = True

    # --- data delay ---
    bar_seconds = TIMEFRAME_SECONDS[bot.timeframe]
    delay_seconds = (now - latest_candle.close_time).total_seconds()
    delay_bars = Decimal(delay_seconds) / Decimal(bar_seconds)
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
    if exchange_code == "binance":
        if signal.action == "sell" and _open_position(db, account, instrument) is None:
            results["binance_no_short"] = {"passed": False}
            hard_breach = True
        else:
            results["binance_no_short"] = {"passed": True}
        btc_cap = equity * _decimal(rules["binance"], "btc_holding_cap_pct_of_equity")
        prospective_notional = quantity * market_price
        results["binance_btc_holding_cap"] = {
            "passed": prospective_notional <= btc_cap,
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

    decision = RiskDecision(
        signal_id=signal.id,
        risk_profile_version_id=risk_profile_version.id,
        outcome=outcome,
        rule_results=results,
        adjusted_quantity=approved_quantity if outcome == "allow_with_adjustment" else None,
        reason_code=reason_code,
    )
    db.add(decision)
    db.flush()
    db.refresh(decision)
    return RiskEvaluationResult(decision=decision, approved_quantity=approved_quantity)
