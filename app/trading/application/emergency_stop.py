"""The user's emergency stop (取引停止マトリクス「利用者の緊急停止」, FR-RISK-03/10).

A person (Operator or Owner) stops one bot, or every bot of a workspace, at once:

- an `emergency_stopped` `trading_halt` is activated (scope `bot` or `workspace`), so no new
  or increasing order can be placed (`order_flow.place_order`) and no bot under it can be
  started or resumed (`bot_lifecycle.validate_bot_startup`);
- the bots under it are stopped (open orders cancelled, `BotRun` ended);
- positions are left as they are, or closed, as the caller chooses (`close_positions`,
  the matrix's "選択ポリシー"). Closing is best effort and happens *after* the stop is
  committed: a missing price must never prevent the stop itself.

Releasing is manual and Owner-only (`trading_halts.py`'s `/emergency-release`); it does not
restart any bot. The call is idempotent: stopping again while the halt is active returns it
unchanged (FR-UI-03: a double click is safe).

The halt, the bot stops and the audit/event rows are one transaction. The halt's announcement
(`trading_halt.activate_or_escalate`: a system event and an outbox row) is what notifies the
workspace's Owners and Operators; see docs/plans/notification-wiring.md.
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.strategy import TradingBot, TradingHalt
from app.models.trading import TradingAccount, TradingPosition
from app.trading.application import bot_lifecycle, order_flow, trading_halt

REASON_CODE = "user_emergency_stop"
_STOP_REASON = "emergency stop"
_SCOPE_LABEL = {"bot": "Bot単位", "workspace": "ワークスペース全体"}


@dataclass
class EmergencyStopResult:
    halt: TradingHalt
    already_active: bool
    stopped_bot_ids: list[UUID] = field(default_factory=list)
    bot_stop_failures: list[dict[str, str]] = field(default_factory=list)
    closing_order_ids: list[UUID] = field(default_factory=list)
    close_failures: list[dict[str, str]] = field(default_factory=list)


def emergency_stop_bot(
    db: Session,
    bot: TradingBot,
    *,
    requested_by: UUID | None,
    close_positions: bool = False,
    reason: str | None = None,
) -> EmergencyStopResult:
    scope = trading_halt.HaltScope(workspace_id=bot.workspace_id, scope_type="bot", scope_id=bot.id)
    return _stop(
        db,
        scope,
        bots=[bot],
        requested_by=requested_by,
        close_positions=close_positions,
        reason=reason,
    )


def emergency_stop_workspace(
    db: Session,
    workspace_id: UUID,
    *,
    requested_by: UUID | None,
    close_positions: bool = False,
    reason: str | None = None,
) -> EmergencyStopResult:
    scope = trading_halt.HaltScope(workspace_id=workspace_id, scope_type="workspace", scope_id=None)
    bots = list(db.scalars(select(TradingBot).where(TradingBot.workspace_id == workspace_id)).all())
    return _stop(
        db,
        scope,
        bots=bots,
        requested_by=requested_by,
        close_positions=close_positions,
        reason=reason,
    )


def _stop(
    db: Session,
    scope: trading_halt.HaltScope,
    *,
    bots: list[TradingBot],
    requested_by: UUID | None,
    close_positions: bool,
    reason: str | None,
) -> EmergencyStopResult:
    try:
        result = _activate_and_stop(db, scope, bots, requested_by, close_positions, reason)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if close_positions:
        _close_open_positions(db, scope, bots, result)
    return result


def _activate_and_stop(
    db: Session,
    scope: trading_halt.HaltScope,
    bots: list[TradingBot],
    requested_by: UUID | None,
    close_positions: bool,
    reason: str | None,
) -> EmergencyStopResult:
    correlation_id = uuid4()
    existing = trading_halt.find_active_halt(db, scope, REASON_CODE)
    scope_label = _SCOPE_LABEL.get(scope.scope_type, scope.scope_type)
    halt = trading_halt.activate_or_escalate(
        db,
        scope,
        reason_code=REASON_CODE,
        level="emergency_stopped",
        auto_releasable=False,
        event=trading_halt.HaltEvent(
            event_type="user_emergency_stop",
            message=f"緊急停止が実行されました({scope_label})",
            payload={"close_positions": close_positions, "reason": reason},
            source_type="user" if requested_by else "operator_script",
            source_id=requested_by,
            correlation_id=correlation_id,
        ),
    )
    result = EmergencyStopResult(halt=halt, already_active=existing is not None)

    for bot in bots:
        if bot.desired_state == "stopped":
            continue
        try:
            with db.begin_nested():
                bot_lifecycle.stop_bot(db, bot, reason=_STOP_REASON, commit=False)
        except bot_lifecycle.BotLifecycleError as exc:
            result.bot_stop_failures.append({"bot_id": str(bot.id), "code": exc.code})
        else:
            result.stopped_bot_ids.append(bot.id)

    db.add(
        AuditLog(
            workspace_id=scope.workspace_id,
            actor_id=requested_by,
            action="trading_halt.user_emergency_stop",
            resource_type="trading_halt",
            resource_id=halt.id,
            before_data=None,
            after_data={
                "scope_type": scope.scope_type,
                "scope_id": str(scope.scope_id) if scope.scope_id else None,
                "already_active": result.already_active,
                "close_positions": close_positions,
                "reason": reason,
                "stopped_bot_ids": [str(bot_id) for bot_id in result.stopped_bot_ids],
                "bot_stop_failures": result.bot_stop_failures,
            },
            correlation_id=correlation_id,
            ip_address=None,
            user_agent=None,
        )
    )
    db.flush()
    return result


def _close_open_positions(
    db: Session,
    scope: trading_halt.HaltScope,
    bots: list[TradingBot],
    result: EmergencyStopResult,
) -> None:
    """One market order per open position of the accounts in scope, each in its own
    transaction so one failure (no price, say) does not stop the others. The halt does not
    block them: `place_order` always lets an order that closes a position through."""
    bot_pairs = {(bot.account_id, bot.instrument_id) for bot in bots}
    positions = db.scalars(
        select(TradingPosition)
        .join(TradingAccount, TradingAccount.id == TradingPosition.account_id)
        .where(
            TradingAccount.workspace_id == scope.workspace_id,
            TradingPosition.status == "open",
        )
    ).all()
    for position in positions:
        if scope.scope_type == "bot" and (
            (position.account_id, position.instrument_id) not in bot_pairs
        ):
            continue
        try:
            order = order_flow.place_order(
                db,
                order_flow.PlaceOrderCommand(
                    workspace_id=scope.workspace_id,
                    account_id=position.account_id,
                    instrument_id=position.instrument_id,
                    side="sell" if position.side == "long" else "buy",
                    order_type="market",
                    quantity=position.quantity,
                    client_order_id=f"emergency-close-{uuid4().hex[:12]}",
                ),
            )
        except Exception as exc:
            db.rollback()
            code = exc.code if isinstance(exc, order_flow.OrderFlowError) else type(exc).__name__
            result.close_failures.append({"position_id": str(position.id), "code": code})
        else:
            result.closing_order_ids.append(order.id)
