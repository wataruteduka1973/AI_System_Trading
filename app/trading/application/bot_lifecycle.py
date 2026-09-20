"""Bot lifecycle commands (start/pause/resume/stop), per
docs/concept/FXtrading_rebuild/05_アーキテクチャと移行計画.md's "Botの状態遷移表" and
"Bot pause/resumeの動作仕様" (both already 確定/2026-09-19). No HTTP layer here --
per 04_API再設計.md "Bot以外からの手動注文はMVPでは提供しない" and the existing
convention this codebase already follows (order_flow.py etc. have no route either),
this module is called directly, not exposed as an endpoint (explicitly out of scope
for this task too).

**Doc correction**: `05_アーキテクチャと移行計画.md`'s "Botの状態遷移表" lists the
`actual_state` enum's terminal failure value as `error`, but the live DB's CHECK
constraint (`database/postgresql_schema_v0.1.sql`, `trading_bot.actual_state`) uses
`failed`. This module uses `failed` (実DBを正とする, the project's existing
convention); the doc should be corrected to match (flagged in the completion report,
not silently fixed here since this module doesn't own that doc section).

**Idempotent 409s** (`05_アーキテクチャと移行計画.md` 2026-09-19決定, "目的の状態に
既に到達しているコマンドは409 Conflictを返し状態は変化させない"): every command
below is a strict state-machine guard, not just an optimization -- calling `start` on
an already-`running` bot (etc.) always raises `BotStateConflictError` and leaves every
row untouched.

**`start` vs `resume`** (disambiguating the doc's own table, which lists `start` as
allowed from both `stopped` and `paused`): this module follows the task's explicit,
narrower split instead -- `start` only accepts `desired_state == "stopped"` and
`resume` only accepts `"paused"` -- so the two commands stay behaviorally distinct
rather than overlapping capabilities, which is what "4つの独立したコマンド" implies
and lets each one's docstring describe one job. Calling `start` on a `paused` bot (or
`resume` on a `stopped` one) is treated as an invalid transition (`invalid_state`),
distinct from the "already there" 409 case.

**pause keeps the same BotRun; stop ends it** (05番の表の該当行そのまま): `pause_bot`
does not create or close a `BotRun`; `resume_bot` continues the same one. Only
`start_bot` (from `stopped`) creates a new `BotRun`, and only `stop_bot` closes one
(`stopped_at`/`stop_reason`).

**"損失回避方向の決済を最優先で実行継続" during pause**: this specific continuation
requirement describes an exit-condition (stop-loss/take-profit) *execution engine*
continuing to run -- not this task's dummy signal generator. This codebase has no
such engine at all yet (`trade_order.stop_price`/`take_profit_price` are stored but
nothing acts on them, independent of Bot lifecycle -- a pre-existing gap noted in
`order_flow.py`'s own module docstring). There is therefore nothing for *this* module
to keep running during pause; see `dummy_pipeline.py`'s docstring for how the
pipeline-layer distinction the task separately asked for ("新規建て玉は行わないが、
既存ポジションの決済シグナルは引き続き評価する") was implemented instead, and the
completion report for why these two requirements are not the same thing.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exchanges.types import TIMEFRAME_SECONDS
from app.models.audit import AuditLog
from app.models.connections import ExchangeConnection
from app.models.market_data import Candle
from app.models.strategy import BotRun, RiskProfileVersion, StrategyVersion, TradingBot
from app.models.trading import TradeOrder, TradingAccount
from app.trading.application import order_flow

_CANCELLABLE_STATUSES = ("pending", "submitted", "partially_filled")
_MAX_DATA_DELAY_BARS = 5  # matches risk_gate.CONSERVATIVE_V1_RULES's hard limit


class BotLifecycleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BotStateConflictError(BotLifecycleError):
    """The bot is already in the state the command would move it to (409-equivalent)."""

    def __init__(self, message: str) -> None:
        super().__init__("already_in_target_state", message)


def validate_bot_startup(db: Session, bot: TradingBot) -> None:
    """ "Bot起動時と同じ事前確認" (データ鮮度、秘密、取引権限、時刻同期、口座照合), run by
    both `start_bot` and `resume_bot`. Raises `BotLifecycleError` on the first failed
    check; does not mutate anything.

    Concrete checks chosen (the doc names five categories but not their concrete
    implementation, so these were designed for this task -- see completion report):
    - データ鮮度: latest final candle for the bot's instrument/timeframe is not more
      than `_MAX_DATA_DELAY_BARS` bars old (mirrors risk_gate.py's hard limit).
    - 秘密: the bot's `ExchangeConnection` has a `secret_ref` and `status == "verified"`.
    - 取引権限: trivially satisfied for `execution_mode == "paper"` (paper never calls
      a real exchange -- FR-ORD-15); `live` is not implemented anywhere in this
      codebase yet, so a live bot always fails this check rather than being silently
      approved.
    - 口座照合: the bot's `TradingAccount` exists and `status == "active"`.
    - 時刻同期: **not implemented** -- no data source for exchange server time exists
      in this paper simulator (found, not fabricated; same category of gap as
      risk_gate.py's omitted OANDA marginCallPercent/marginCloseoutPercent checks)."""
    connection = db.get(ExchangeConnection, bot.connection_id)
    if connection is None or connection.secret_ref is None or connection.status != "verified":
        raise BotLifecycleError(
            "startup_validation_failed_secrets",
            "Exchange connection is missing, has no stored credentials, or is not verified",
        )

    if bot.execution_mode != "paper":
        raise BotLifecycleError(
            "startup_validation_failed_trading_permission",
            "Only execution_mode='paper' bots are supported; live is not implemented",
        )

    account = db.get(TradingAccount, bot.account_id)
    if account is None or account.status != "active":
        raise BotLifecycleError(
            "startup_validation_failed_account", "Trading account is missing or not active"
        )

    strategy_version = db.get(StrategyVersion, bot.strategy_version_id)
    if strategy_version is None or strategy_version.lifecycle_status not in (
        "paper_approved",
        "live_approved",
    ):
        raise BotLifecycleError(
            "startup_validation_failed_strategy",
            "strategy_version.lifecycle_status must be paper_approved or later",
        )

    risk_profile_version = db.get(RiskProfileVersion, bot.risk_profile_version_id)
    if risk_profile_version is None or risk_profile_version.status != "approved":
        raise BotLifecycleError(
            "startup_validation_failed_risk_profile", "risk_profile_version.status must be approved"
        )

    latest_candle = db.scalar(
        select(Candle)
        .where(
            Candle.instrument_id == bot.instrument_id,
            Candle.timeframe == bot.timeframe,
            Candle.is_final.is_(True),
        )
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if latest_candle is None:
        raise BotLifecycleError(
            "startup_validation_failed_data_freshness",
            "No final candle available for this instrument",
        )
    bar_seconds = TIMEFRAME_SECONDS[bot.timeframe]
    delay_bars = (datetime.now(UTC) - latest_candle.close_time).total_seconds() / bar_seconds
    if delay_bars > _MAX_DATA_DELAY_BARS:
        raise BotLifecycleError(
            "startup_validation_failed_data_freshness",
            f"Latest candle is {delay_bars:.1f} bars old (limit {_MAX_DATA_DELAY_BARS})",
        )


def _cancel_open_orders(db: Session, bot: TradingBot, *, reason_code: str) -> list[TradeOrder]:
    """Cancels every cancellable order for the bot's account. Queried by `account_id`
    rather than joining through OrderIntent->Signal->BotRun->bot_id: a direct/manual
    order (`order_intent_id=None`, e.g. a dote-gating close-only order -- see
    dummy_pipeline.py) has no such chain to join through, and this codebase's current
    design has exactly one TradingBot per TradingAccount, so account_id is both
    simpler and more complete here. Under the current fully-synchronous fill model
    (order_flow.py: every order reaches a terminal state inside one `place_order`
    call) this is a no-op in practice -- nothing is ever left pending -- but is
    implemented correctly for when non-instant (e.g. limit) orders exist."""
    orders = db.scalars(
        select(TradeOrder).where(
            TradeOrder.account_id == bot.account_id, TradeOrder.status.in_(_CANCELLABLE_STATUSES)
        )
    ).all()
    return [order_flow.cancel_order(db, order, reason_code=reason_code) for order in orders]


def _audit(db: Session, bot: TradingBot, action: str, after_data: dict[str, object]) -> None:
    db.add(
        AuditLog(
            workspace_id=bot.workspace_id,
            actor_id=None,
            action=action,
            resource_type="trading_bot",
            resource_id=bot.id,
            before_data=None,
            after_data=after_data,
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )


def start_bot(db: Session, bot: TradingBot) -> BotRun:
    """`stopped` -> `running`, creating a new `BotRun`."""
    if bot.desired_state == "running":
        raise BotStateConflictError("Bot is already running")
    if bot.desired_state != "stopped":
        raise BotLifecycleError(
            "invalid_state", f"start requires desired_state='stopped' (was '{bot.desired_state}')"
        )
    validate_bot_startup(db, bot)

    now = datetime.now(UTC)
    bot_run = BotRun(
        bot_id=bot.id, status="running", code_version="dummy-pipeline-0.1", started_at=now
    )
    db.add(bot_run)
    db.flush()
    bot.desired_state = "running"
    bot.actual_state = "running"
    bot.version += 1
    bot.updated_at = now
    _audit(db, bot, "trading_bot.started", {"bot_run_id": str(bot_run.id)})
    db.commit()
    db.refresh(bot_run)
    return bot_run


def pause_bot(db: Session, bot: TradingBot) -> BotRun:
    """`running` -> `paused`. Cancels open orders; keeps the same `BotRun`."""
    if bot.desired_state == "paused":
        raise BotStateConflictError("Bot is already paused")
    if bot.desired_state != "running":
        raise BotLifecycleError(
            "invalid_state", f"pause requires desired_state='running' (was '{bot.desired_state}')"
        )
    bot_run = db.scalar(select(BotRun).where(BotRun.bot_id == bot.id, BotRun.status == "running"))
    if bot_run is None:
        raise BotLifecycleError("no_active_bot_run", "No running BotRun found for this bot")

    cancelled = _cancel_open_orders(db, bot, reason_code="bot_paused")
    now = datetime.now(UTC)
    bot.desired_state = "paused"
    bot.actual_state = "paused"
    bot.version += 1
    bot.updated_at = now
    bot_run.status = "paused"
    bot_run.heartbeat_at = now
    _audit(db, bot, "trading_bot.paused", {"cancelled_order_ids": [str(o.id) for o in cancelled]})
    db.commit()
    db.refresh(bot_run)
    return bot_run


def resume_bot(db: Session, bot: TradingBot) -> BotRun:
    """`paused` -> `running`, after re-running the same startup validation as `start`.
    Continues the same `BotRun` `pause_bot` left in place (does not create a new one)."""
    if bot.desired_state == "running":
        raise BotStateConflictError("Bot is already running")
    if bot.desired_state != "paused":
        raise BotLifecycleError(
            "invalid_state", f"resume requires desired_state='paused' (was '{bot.desired_state}')"
        )
    bot_run = db.scalar(select(BotRun).where(BotRun.bot_id == bot.id, BotRun.status == "paused"))
    if bot_run is None:
        raise BotLifecycleError("no_paused_bot_run", "No paused BotRun found for this bot")

    validate_bot_startup(db, bot)  # failure here leaves state untouched -- raises, no mutation yet

    now = datetime.now(UTC)
    bot.desired_state = "running"
    bot.actual_state = "running"
    bot.version += 1
    bot.updated_at = now
    bot_run.status = "running"
    bot_run.heartbeat_at = now
    _audit(db, bot, "trading_bot.resumed", {"bot_run_id": str(bot_run.id)})
    db.commit()
    db.refresh(bot_run)
    return bot_run


def stop_bot(db: Session, bot: TradingBot, *, reason: str | None = None) -> BotRun:
    """`running`/`paused` -> `stopped`. Cancels open orders; ends the `BotRun`."""
    if bot.desired_state == "stopped":
        raise BotStateConflictError("Bot is already stopped")
    if bot.desired_state not in ("running", "paused"):
        raise BotLifecycleError(
            "invalid_state", f"Cannot stop a bot with desired_state='{bot.desired_state}'"
        )
    bot_run = db.scalar(
        select(BotRun).where(BotRun.bot_id == bot.id, BotRun.status.in_(("running", "paused")))
    )
    if bot_run is None:
        raise BotLifecycleError("no_active_bot_run", "No active BotRun found for this bot")

    cancelled = _cancel_open_orders(db, bot, reason_code="bot_stopped")
    now = datetime.now(UTC)
    bot.desired_state = "stopped"
    bot.actual_state = "stopped"
    bot.version += 1
    bot.updated_at = now
    bot_run.status = "stopped"
    bot_run.stopped_at = now
    bot_run.stop_reason = reason or "stop command"
    _audit(
        db,
        bot,
        "trading_bot.stopped",
        {"cancelled_order_ids": [str(o.id) for o in cancelled], "reason": bot_run.stop_reason},
    )
    db.commit()
    db.refresh(bot_run)
    return bot_run
