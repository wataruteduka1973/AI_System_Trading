"""Orchestrates one evaluation cycle of a paper bot: the signal from the bot's
strategy version (`live_strategies.resolve_live_strategy`) -> Risk Gate ->
OrderIntent -> `order_flow.place_order`, plus the stop-loss check before it.

Formerly `dummy_pipeline.run_dummy_pipeline_once` (renamed 2026-10-03), from when the
only signal was the SMA pipeline skeleton; older docs and the stored identifiers
`code_version="dummy-pipeline-0.1"` and `client_order_id="dummy-..."` keep that name.

Bots are created elsewhere (`paper_provisioning.py`) and left stopped; starting one
is `bot_lifecycle.start_bot`'s job.

**`evaluate_bot_on_latest_bar`'s gating on `bot.actual_state`** (2026-09-20, added for
the Bot lifecycle task): `stopped` skips everything, including signal generation
itself, before touching candles at all -- matching
05_アーキテクチャと移行計画.md"Bot pause/resumeの動作仕様"'s "新しいシグナル評価...
停止する" literally. `paused` is handled differently, per this task's own explicit
instruction to still implement "新規建て玉は行わないが、既存ポジションの決済シグナ
ルは引き続き評価する" as a minimum: a signal is still generated and, if it opposes
the held position, still results in a close-only order exactly as it would while
`running`; only a *new or same-direction* entry is suppressed while paused. This is a
narrower reading of "新しいシグナル評価" than the doc's own sentence taken in
isolation -- see `bot_lifecycle.py`'s module docstring for the full reasoning on why
these two requirements (a real exit-monitoring engine continuing vs. this pipeline's
own gating) are not actually the same thing, and the completion report for this being
flagged as a doc/task tension rather than resolved silently.

**Dote-gating (§ the task's explicit request), simplified from the original plan**:
when the new signal opposes an existing position, this module places a close-only
order (quantity capped to exactly the held quantity, so `order_flow.py` closes rather
than flips) and does **not** evaluate/open the reverse position in the same call. No
extra "pending confirmation" state is tracked for this: because closing makes the
account flat, the *next* call to `evaluate_bot_on_latest_bar` (on the next bar) finds no
open position and evaluates the new signal as an ordinary fresh entry -- which is
already exactly "only open the reverse position if the same-direction signal still
holds on the next evaluation." Tracking an explicit pending-reversal marker (as the
approved plan sketched, via an `AuditLog` row) turned out to be unnecessary: it would
have reconstructed information this natural two-call statefulness already provides.
An informational `AuditLog` entry is still written when a close-only happens, for
audit visibility -- it is not read back by anything.

**One evaluation cycle is one transaction** (2026-10-06): the stop-loss close, the Signal,
the RiskDecision (and any trading_halt it sets), the OrderIntent, the order with its fill,
position, ledger and snapshot rows, and the stop on a new position are committed together
by `evaluate_bot_on_latest_bar`, or rolled back together if anything raises. A failure no
longer leaves a Signal committed without its order -- which, with the per-candle idempotency
below, would have made the Worker skip that candle on every later poll. Now the whole cycle
is retried on the next poll. A denied or held cycle is not a failure: its Signal and
RiskDecision are committed. `order_flow` is called with `commit=False` for this.
"""

import hashlib
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.connections import Exchange, ExchangeConnection
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import (
    BotRun,
    RiskProfileVersion,
    Signal,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import Fill, TradeOrder, TradingAccount, TradingPosition
from app.trading.application import order_flow
from app.trading.application.backtest_fill import BacktestPosition
from app.trading.application.backtest_replay import _HISTORY_WINDOW, _protective_exit
from app.trading.application.live_strategies import resolve_live_strategy
from app.trading.application.risk_gate import evaluate_signal


def _checksum(payload: object) -> str:
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _exchange_code_for_connection(db: Session, connection_id: UUID) -> str:
    code = db.scalar(
        select(Exchange.code)
        .join(ExchangeConnection, ExchangeConnection.exchange_id == Exchange.id)
        .where(ExchangeConnection.id == connection_id)
    )
    if code is None:
        raise ValueError("could not resolve exchange code for this connection")
    return code


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


def _stop_exit_price(candles: list[Candle], position: TradingPosition) -> Decimal | None:
    """Where `position`'s stop-loss filled, or None if it has not been hit. Only
    bars that closed after the position was opened are checked -- the entry bar's
    own range happened before the entry filled at its close -- and the first bar
    that reaches the stop wins, so bars missed while the worker was down are not
    skipped. Uses the backtest's own rule (`backtest_replay._protective_exit`):
    the stop price, or a gapped bar's open."""
    if position.stop_price is None or position.opened_at is None:
        return None
    if position.average_entry_price is None or position.side not in ("long", "short"):
        return None
    held = BacktestPosition(
        side="long" if position.side == "long" else "short",
        quantity=position.quantity,
        average_entry_price=position.average_entry_price,
    )
    for candle in candles:
        if candle.close_time <= position.opened_at:
            continue
        hit = _protective_exit(candle, held, position.stop_price, None)
        if hit is not None:
            return hit[0]
    return None


def _entry_stop_price(side: str, fill_price: Decimal, stop_distance: Decimal) -> Decimal | None:
    """The stop for a position opened from flat: `stop_distance` (the distance the
    Risk Gate sized the entry for) away from the fill, as in the backtest. None if
    that would not be a positive price."""
    stop = fill_price - stop_distance if side == "buy" else fill_price + stop_distance
    return stop if stop > 0 else None


def _close_at_stop(
    db: Session,
    bot: TradingBot,
    account: TradingAccount,
    instrument: Instrument,
    candles: list[Candle],
) -> None:
    """Closes the whole open position at its stop if a bar since entry reached it
    (docs/plans/paper-trading-live-data.md Unit 3). Runs before the signal, as the
    backtest checks stops before evaluating a bar's signal; a paused bot still
    closes (closing is always allowed), a stopped bot never gets here."""
    position = _open_position(db, account, instrument)
    if position is None:
        return
    exit_price = _stop_exit_price(candles, position)
    if exit_price is None:
        return
    order_flow.place_order(
        db,
        order_flow.PlaceOrderCommand(
            workspace_id=bot.workspace_id,
            account_id=account.id,
            instrument_id=instrument.id,
            side="sell" if position.side == "long" else "buy",
            order_type="market",
            quantity=position.quantity,
            client_order_id=f"stop-{uuid4().hex[:12]}",
            stop_price=position.stop_price,
            stop_fill_price=exit_price,
            bot_id=bot.id,
        ),
        commit=False,
    )
    db.add(
        AuditLog(
            workspace_id=bot.workspace_id,
            actor_id=None,
            action="protective_exit.stop_loss",
            resource_type="trading_position",
            resource_id=position.id,
            before_data=None,
            after_data={"stop_price": str(position.stop_price), "fill_price": str(exit_price)},
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )


def _set_entry_stop(
    db: Session,
    account: TradingAccount,
    instrument: Instrument,
    order: TradeOrder,
    side: str,
    stop_distance: Decimal,
) -> None:
    """Records the stop on a position this bot just opened from flat. An entry
    that adds to an existing position keeps the original stop (the caller only
    calls this from flat), as in the backtest."""
    fill = db.scalar(select(Fill).where(Fill.order_id == order.id))
    position = _open_position(db, account, instrument)
    if fill is None or position is None:
        return
    position.stop_price = _entry_stop_price(side, fill.price, stop_distance)


def evaluate_bot_on_latest_bar(db: Session, bot: TradingBot, bot_run: BotRun) -> dict:
    """Runs one evaluation cycle for `bot` as a single transaction (see the module
    docstring): committed once on success, rolled back entirely on any exception.
    Returns a small dict describing what
    happened (`action`: "hold" | "already_processed" | "close_only" | "denied" |
    "opened", plus the row ids involved) -- meant for tests/scripts to assert
    against, not a public API.

    Gated on `bot.actual_state` -- see module docstring for the stopped-vs-paused
    distinction (stopped skips everything below before even generating a signal;
    paused still generates one and still allows a close-only, just not a new entry).

    **Idempotent per (bot_run_id, candle_id)** (2026-09-25, added for the execution
    loop/Worker task): the Worker calling this repeatedly on a poll interval will
    often see the same still-latest final candle more than once before a new one
    closes. Without a guard, a second call for the same candle would call
    the strategy's signal generator again (deterministic, same result) and then fail on
    the final commit with an uncaught `IntegrityError` against
    `uq_signal_idempotency` -- this function had no callers before the Worker, so
    that path was never exercised. Returning "already_processed" early makes
    repeated polling safe without the Worker needing to track per-bot state itself."""
    try:
        result = _evaluate(db, bot, bot_run)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def _evaluate(db: Session, bot: TradingBot, bot_run: BotRun) -> dict:
    """The cycle itself; never commits (the caller owns the transaction)."""
    if bot.actual_state not in ("running", "paused"):
        return {"action": "hold", "reason": "bot_not_active", "actual_state": bot.actual_state}

    account = db.get(TradingAccount, bot.account_id)
    instrument = db.get(Instrument, bot.instrument_id)
    if account is None or instrument is None:
        raise ValueError("bot's account or instrument no longer exists")
    exchange_code = _exchange_code_for_connection(db, bot.connection_id)

    candles = list(
        db.scalars(
            select(Candle)
            .where(
                Candle.instrument_id == instrument.id,
                Candle.timeframe == bot.timeframe,
                Candle.is_final.is_(True),
            )
            .order_by(Candle.open_time.desc())
            .limit(_HISTORY_WINDOW)
        )
    )
    candles.reverse()
    if not candles:
        return {"action": "hold", "reason": "no_candles"}
    latest_candle = candles[-1]

    already_processed = db.scalar(
        select(Signal).where(Signal.bot_run_id == bot_run.id, Signal.candle_id == latest_candle.id)
    )
    if already_processed is not None:
        return {"action": "already_processed", "signal_id": already_processed.id}

    strategy_version = db.get(StrategyVersion, bot.strategy_version_id)
    if strategy_version is None:
        raise ValueError("bot's strategy_version no longer exists")
    strategy = resolve_live_strategy(strategy_version.definition)
    _close_at_stop(db, bot, account, instrument, candles)
    signal_action = strategy.generate(candles)
    input_checksum = _checksum(
        {"bot_run_id": str(bot_run.id), "candle_id": str(latest_candle.id), "action": signal_action}
    )
    signal = Signal(
        workspace_id=bot.workspace_id,
        bot_run_id=bot_run.id,
        candle_id=latest_candle.id,
        strategy_version_id=bot.strategy_version_id,
        action=signal_action,
        rationale=strategy.rationale(latest_candle),
        input_checksum=input_checksum,
    )
    db.add(signal)
    db.flush()
    db.refresh(signal)

    if signal_action == "hold":
        return {"action": "hold", "signal_id": signal.id}

    existing = _open_position(db, account, instrument)
    opposing = existing is not None and (
        (signal_action == "buy" and existing.side == "short")
        or (signal_action == "sell" and existing.side == "long")
    )

    if opposing:
        assert existing is not None
        order = order_flow.place_order(
            db,
            order_flow.PlaceOrderCommand(
                workspace_id=bot.workspace_id,
                account_id=account.id,
                instrument_id=instrument.id,
                side=signal_action,
                order_type="market",
                quantity=existing.quantity,
                client_order_id=f"dote-close-{uuid4().hex[:12]}",
                bot_id=bot.id,
            ),
            commit=False,
        )
        db.add(
            AuditLog(
                workspace_id=bot.workspace_id,
                actor_id=None,
                action="dote_gate.close_only",
                resource_type="trading_position",
                resource_id=existing.id,
                before_data=None,
                after_data={"signal_id": str(signal.id), "opposing_action": signal_action},
                correlation_id=uuid4(),
                ip_address=None,
                user_agent=None,
            )
        )
        return {"action": "close_only", "signal_id": signal.id, "order_id": order.id}

    if bot.actual_state == "paused":
        # Paused: closing (above) is still allowed, but a new or same-direction
        # entry is not -- see module docstring's "evaluate_bot_on_latest_bar's gating".
        return {
            "action": "hold",
            "signal_id": signal.id,
            "reason": "bot_paused_no_new_entries",
        }

    risk_profile_version = db.get(RiskProfileVersion, bot.risk_profile_version_id)
    if risk_profile_version is None:
        raise ValueError("bot's risk_profile_version no longer exists")
    result = evaluate_signal(
        db, signal, account, instrument, bot, risk_profile_version, exchange_code
    )
    if result.decision.outcome == "deny":
        return {
            "action": "denied",
            "signal_id": signal.id,
            "risk_decision_id": result.decision.id,
            "reason_code": result.decision.reason_code,
        }

    assert result.approved_quantity is not None
    stop_distance = Decimal(result.decision.rule_results["quantity_calculation"]["stop_distance"])

    intent = order_flow.create_order_intent(
        db,
        order_flow.OrderIntentCommand(
            signal_id=signal.id,
            risk_decision_id=result.decision.id,
            side=signal_action,
            order_type="market",
            requested_quantity=result.approved_quantity,
        ),
        commit=False,
    )
    order = order_flow.place_order(
        db,
        order_flow.PlaceOrderCommand(
            workspace_id=bot.workspace_id,
            account_id=account.id,
            instrument_id=instrument.id,
            side=signal_action,
            order_type="market",
            quantity=result.approved_quantity,
            client_order_id=f"dummy-{uuid4().hex[:12]}",
            order_intent_id=intent.id,
            bot_id=bot.id,
        ),
        commit=False,
    )
    if strategy.exit_policy == "stop_loss" and existing is None:
        _set_entry_stop(db, account, instrument, order, signal_action, stop_distance)
    return {
        "action": "opened",
        "signal_id": signal.id,
        "risk_decision_id": result.decision.id,
        "order_intent_id": intent.id,
        "order_id": order.id,
    }
