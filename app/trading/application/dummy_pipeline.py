"""Orchestrates one evaluation cycle of the placeholder pipeline: dummy signal ->
Risk Gate -> OrderIntent -> `order_flow.place_order`. See `dummy_signal.py`'s
docstring -- this whole module is pipeline-wiring scaffolding for exercising
Signal -> RiskDecision -> OrderIntent -> TradeOrder -> Fill end-to-end while a real
predictive model is on hold, not a strategy implementation.

`ensure_dummy_bot` creates the minimum fixture rows the schema's foreign keys require
to record a Signal at all (`Strategy`/`StrategyVersion`, `RiskProfile`/
`RiskProfileVersion`, `TradingBot`). Since `bot_lifecycle.py` now exists, it creates
the `TradingBot` in `stopped` state and calls `bot_lifecycle.start_bot` to bring it
up (creating the first `BotRun` through the real lifecycle path, including startup
validation -- see that module) rather than writing `running`/`BotRun` fields directly
as an earlier version of this function did; a bot found already `running`/`paused`
just has its current `BotRun` looked up instead. Still idempotent (safe to call
repeatedly).

**`run_dummy_pipeline_once`'s gating on `bot.actual_state`** (2026-09-20, added for
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
account flat, the *next* call to `run_dummy_pipeline_once` (on the next bar) finds no
open position and evaluates the new signal as an ordinary fresh entry -- which is
already exactly "only open the reverse position if the same-direction signal still
holds on the next evaluation." Tracking an explicit pending-reversal marker (as the
approved plan sketched, via an `AuditLog` row) turned out to be unnecessary: it would
have reconstructed information this natural two-call statefulness already provides.
An informational `AuditLog` entry is still written when a close-only happens, for
audit visibility -- it is not read back by anything.

**Known limitation**: each stage below (Signal write, `create_order_intent`,
`place_order`) commits its own transaction rather than the whole cycle being one
atomic unit -- `order_flow.py`'s entry points are each independently
self-committing by design (see their own docstrings), and composing them from here
does not change that. A failure partway through a cycle can therefore leave a Signal
row with no `RiskDecision`/order after it, which is a valid DB state (nothing
requires every Signal to have one) but not a full rollback of "this evaluation cycle
never happened."
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
    RiskProfile,
    RiskProfileVersion,
    Signal,
    Strategy,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import TradingAccount, TradingPosition
from app.trading.application import order_flow
from app.trading.application.bot_lifecycle import start_bot
from app.trading.application.dummy_signal import generate_dummy_signal
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES, evaluate_signal


def _checksum(payload: object) -> str:
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def ensure_dummy_bot(
    db: Session,
    workspace_id: UUID,
    account: TradingAccount,
    instrument: Instrument,
    *,
    timeframe: str = "1m",
    strategy_name: str = "dummy-sma-pipeline-skeleton",
    risk_profile_name: str = "conservative-v1-dummy",
    bot_name: str = "dummy-sma-bot",
) -> tuple[TradingBot, BotRun]:
    """Idempotent: reuses existing rows by (workspace_id, name) if this has already
    been called for this workspace."""
    strategy = db.scalar(
        select(Strategy).where(
            Strategy.workspace_id == workspace_id, Strategy.name == strategy_name
        )
    )
    if strategy is None:
        strategy = Strategy(workspace_id=workspace_id, name=strategy_name, mode="technical")
        db.add(strategy)
        db.flush()
    strategy_version = db.scalar(
        select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id)
    )
    if strategy_version is None:
        definition = {
            "kind": "dummy_sma_crossover",
            "period": 5,
            "note": "pipeline skeleton only, not a real strategy -- see dummy_signal.py",
        }
        strategy_version = StrategyVersion(
            strategy_id=strategy.id,
            version=1,
            supported_market_types=["foreign_fx", "crypto"],
            definition=definition,
            checksum=_checksum(definition),
            # "paper_approved" (not the DB default "draft"): bot_lifecycle.py's
            # startup validation requires this before a bot can start/resume. This
            # dummy strategy genuinely is approved for paper use -- that is the
            # whole point of this pipeline skeleton -- so this is not a fabricated
            # bypass of the gate, just an honest status for what it is.
            lifecycle_status="paper_approved",
        )
        db.add(strategy_version)
        db.flush()

    risk_profile = db.scalar(
        select(RiskProfile).where(
            RiskProfile.workspace_id == workspace_id, RiskProfile.name == risk_profile_name
        )
    )
    if risk_profile is None:
        risk_profile = RiskProfile(workspace_id=workspace_id, name=risk_profile_name)
        db.add(risk_profile)
        db.flush()
    risk_profile_version = db.scalar(
        select(RiskProfileVersion).where(RiskProfileVersion.risk_profile_id == risk_profile.id)
    )
    if risk_profile_version is None:
        risk_profile_version = RiskProfileVersion(
            risk_profile_id=risk_profile.id,
            version=1,
            rules=CONSERVATIVE_V1_RULES,
            checksum=_checksum(CONSERVATIVE_V1_RULES),
            status="approved",
        )
        db.add(risk_profile_version)
        db.flush()

    bot = db.scalar(
        select(TradingBot).where(
            TradingBot.workspace_id == workspace_id, TradingBot.name == bot_name
        )
    )
    if bot is None:
        if account.connection_id is None:
            raise ValueError("account has no connection_id; cannot create a TradingBot")
        bot = TradingBot(
            workspace_id=workspace_id,
            name=bot_name,
            execution_mode="paper",
            strategy_mode="technical",
            connection_id=account.connection_id,
            account_id=account.id,
            instrument_id=instrument.id,
            timeframe=timeframe,
            strategy_version_id=strategy_version.id,
            risk_profile_version_id=risk_profile_version.id,
            # DB defaults (stopped/stopped): start_bot below brings it up through
            # the real lifecycle path instead of setting running fields directly.
        )
        db.add(bot)
        db.flush()
    else:
        db.commit()  # persist any strategy/risk_profile rows created above

    bot_run: BotRun
    if bot.desired_state == "stopped":
        bot_run = start_bot(db, bot)
    else:
        found = db.scalar(
            select(BotRun).where(BotRun.bot_id == bot.id, BotRun.status.in_(("running", "paused")))
        )
        if found is None:
            raise ValueError(f"bot desired_state={bot.desired_state!r} but has no active BotRun")
        bot_run = found

    db.refresh(bot)
    db.refresh(bot_run)
    return bot, bot_run


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


def run_dummy_pipeline_once(db: Session, bot: TradingBot, bot_run: BotRun) -> dict:
    """Runs one evaluation cycle for `bot`. Returns a small dict describing what
    happened (`action`: "hold" | "close_only" | "denied" | "opened", plus the row ids
    involved) -- meant for tests/scripts to assert against, not a public API.

    Gated on `bot.actual_state` -- see module docstring for the stopped-vs-paused
    distinction (stopped skips everything below before even generating a signal;
    paused still generates one and still allows a close-only, just not a new entry)."""
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
            .limit(30)
        )
    )
    candles.reverse()
    if not candles:
        return {"action": "hold", "reason": "no_candles"}
    latest_candle = candles[-1]

    signal_action = generate_dummy_signal(candles)
    input_checksum = _checksum(
        {"bot_run_id": str(bot_run.id), "candle_id": str(latest_candle.id), "action": signal_action}
    )
    signal = Signal(
        workspace_id=bot.workspace_id,
        bot_run_id=bot_run.id,
        candle_id=latest_candle.id,
        strategy_version_id=bot.strategy_version_id,
        action=signal_action,
        rationale={"generator": "dummy_sma", "period": 5, "close": str(latest_candle.close)},
        input_checksum=input_checksum,
    )
    db.add(signal)
    db.commit()
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
        db.commit()
        return {"action": "close_only", "signal_id": signal.id, "order_id": order.id}

    if bot.actual_state == "paused":
        # Paused: closing (above) is still allowed, but a new or same-direction
        # entry is not -- see module docstring's "run_dummy_pipeline_once's gating".
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
    db.commit()
    if result.decision.outcome == "deny":
        return {
            "action": "denied",
            "signal_id": signal.id,
            "risk_decision_id": result.decision.id,
            "reason_code": result.decision.reason_code,
        }

    assert result.approved_quantity is not None
    stop_distance = Decimal(result.decision.rule_results["quantity_calculation"]["stop_distance"])
    reward_multiple = Decimal("2.0")  # matches risk_gate.py's hardcoded reward/risk check
    take_profit_price = (
        latest_candle.close + stop_distance * reward_multiple
        if signal_action == "buy"
        else latest_candle.close - stop_distance * reward_multiple
    )

    intent = order_flow.create_order_intent(
        db,
        order_flow.OrderIntentCommand(
            signal_id=signal.id,
            risk_decision_id=result.decision.id,
            side=signal_action,
            order_type="market",
            requested_quantity=result.approved_quantity,
            take_profit_price=take_profit_price,
        ),
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
    )
    return {
        "action": "opened",
        "signal_id": signal.id,
        "risk_decision_id": result.decision.id,
        "order_intent_id": intent.id,
        "order_id": order.id,
    }
