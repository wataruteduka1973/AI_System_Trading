"""What the system is doing right now, for one workspace, in one call (docs/plans/system-status.md).

Everything here is read off rows the workers already keep -- bot heartbeats, subscription due
times, the outbox, the halts -- so it costs the workers nothing and cannot disagree with the
alerts: the stalled-worker checks are the very queries `worker_watchdog` raises its alerts from,
with the same thresholds. The difference is that an alert fires once per outage, while this answers
"is it healthy now?" every time it is asked, which is what a status page needs.

`problems` is a ready-to-show list in Japanese, empty when all is well; `overall` is `ok` or
`attention`. A part with nothing to do (no active bots, no enabled subscriptions) is `idle`, not
`ok`: a stopped system is not a healthy one, but it is not broken either.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.market_data.application.worker_watchdog import find_overdue_subscriptions
from app.models.audit import OutboxEvent, SystemEvent
from app.models.connections import ExchangeConnection
from app.models.market_data import MarketDataSubscription
from app.models.strategy import BotRun, TradingBot, TradingHalt
from app.trading.application.worker_watchdog import find_stalled_bots

_FAILED_OUTBOX_WINDOW = timedelta(days=7)
_PENDING_NOTIFICATION_GRACE_SECONDS = 300


@dataclass
class TradingWorkerStatus:
    status: str  # "ok" | "stalled" | "idle"
    active_bots: int
    last_heartbeat_age_seconds: int | None
    stalled_bots: list[str]
    stale_after_seconds: int


@dataclass
class MarketDataWorkerStatus:
    status: str  # "ok" | "stalled" | "idle"
    enabled_subscriptions: int
    blocked_subscriptions: int
    overdue_subscriptions: int
    most_overdue_seconds: int | None
    overdue_after_seconds: int


@dataclass
class NotificationStatus:
    pending_events: int
    oldest_pending_age_seconds: int | None
    failed_events_last_7_days: int


@dataclass
class ConnectionStatus:
    id: UUID
    label: str
    environment: str
    status: str
    verification_outcome: str
    last_verified_at: datetime | None


@dataclass
class SystemStatus:
    checked_at: datetime
    overall: str
    problems: list[str]
    trading_worker: TradingWorkerStatus
    market_data_worker: MarketDataWorkerStatus
    notifications: NotificationStatus
    halts: dict[str, int]
    bots: dict[str, int]
    connections: list[ConnectionStatus]


def _age_seconds(now: datetime, then: datetime | None) -> int | None:
    return None if then is None else max(0, int((now - then).total_seconds()))


def _trading_worker(
    db: Session, workspace_id: UUID, now: datetime, stale_after: timedelta
) -> TradingWorkerStatus:
    last_seen = func.coalesce(BotRun.heartbeat_at, BotRun.started_at)
    active, newest = db.execute(
        select(func.count(), func.max(last_seen))
        .select_from(TradingBot)
        .join(BotRun, BotRun.bot_id == TradingBot.id)
        .where(
            TradingBot.workspace_id == workspace_id,
            TradingBot.actual_state.in_(("running", "paused")),
            BotRun.status.in_(("running", "paused")),
        )
    ).one()
    stalled = [
        name
        for _, name, _ in find_stalled_bots(
            db, now=now, stale_after=stale_after, workspace_id=workspace_id
        )
    ]
    return TradingWorkerStatus(
        status="idle" if not active else ("stalled" if stalled else "ok"),
        active_bots=active,
        last_heartbeat_age_seconds=_age_seconds(now, newest),
        stalled_bots=stalled,
        stale_after_seconds=int(stale_after.total_seconds()),
    )


def _market_data_worker(
    db: Session, workspace_id: UUID, now: datetime, overdue_after: timedelta
) -> MarketDataWorkerStatus:
    enabled, blocked = db.execute(
        select(
            func.count().filter(MarketDataSubscription.blocked_reason.is_(None)),
            func.count().filter(MarketDataSubscription.blocked_reason.is_not(None)),
        ).where(
            MarketDataSubscription.workspace_id == workspace_id,
            MarketDataSubscription.enabled.is_(True),
        )
    ).one()
    overdue = find_overdue_subscriptions(
        db, now=now, overdue_after=overdue_after, workspace_id=workspace_id
    )
    return MarketDataWorkerStatus(
        status="idle" if not enabled else ("stalled" if overdue else "ok"),
        enabled_subscriptions=enabled,
        blocked_subscriptions=blocked,
        overdue_subscriptions=len(overdue),
        most_overdue_seconds=_age_seconds(now, overdue[0][3]) if overdue else None,
        overdue_after_seconds=int(overdue_after.total_seconds()),
    )


def _notifications(db: Session, workspace_id: UUID, now: datetime) -> NotificationStatus:
    """Outbox rows of this workspace's events (matched to their `SystemEvent` by
    `correlation_id`; the outbox itself has no workspace)."""
    base = (
        select(func.count(), func.min(OutboxEvent.created_at))
        .select_from(OutboxEvent)
        .join(SystemEvent, SystemEvent.correlation_id == OutboxEvent.correlation_id)
        .where(SystemEvent.workspace_id == workspace_id)
    )
    pending, oldest = db.execute(base.where(OutboxEvent.status == "pending")).one()
    failed, _ = db.execute(
        base.where(
            OutboxEvent.status == "failed", OutboxEvent.created_at > now - _FAILED_OUTBOX_WINDOW
        )
    ).one()
    return NotificationStatus(
        pending_events=pending,
        oldest_pending_age_seconds=_age_seconds(now, oldest),
        failed_events_last_7_days=failed,
    )


def _problems(
    trading: TradingWorkerStatus,
    market: MarketDataWorkerStatus,
    notifications: NotificationStatus,
    bots: dict[str, int],
    halts: dict[str, int],
) -> list[str]:
    problems: list[str] = []
    if trading.status == "stalled":
        problems.append(
            "トレーディングWorkerが止まっている可能性があります"
            f"(対象Bot: {', '.join(trading.stalled_bots[:5])})"
        )
    if bots.get("failed"):
        problems.append(f"評価に失敗して停止したBotが{bots['failed']}件あります")
    if market.status == "stalled":
        problems.append(
            "市場データWorkerが止まっている可能性があります"
            f"(取得が遅れている購読: {market.overdue_subscriptions}件)"
        )
    if market.blocked_subscriptions:
        problems.append(f"停止している自動取得が{market.blocked_subscriptions}件あります")
    for level, label in (
        ("emergency_stopped", "緊急停止中"),
        ("all_trading_halted", "全取引の停止中"),
        ("entry_halted", "新規建玉の停止中"),
    ):
        if halts.get(level):
            problems.append(f"{label}の取引停止(halt)が{halts[level]}件あります")
    if (
        notifications.pending_events
        and (notifications.oldest_pending_age_seconds or 0) > _PENDING_NOTIFICATION_GRACE_SECONDS
    ):
        problems.append(
            f"通知が{notifications.pending_events}件、5分以上配信されていません"
            "(通知Workerが止まっているか、メールの配信に失敗しています)"
        )
    if notifications.failed_events_last_7_days:
        problems.append(
            f"届けられなかった通知が、直近7日に{notifications.failed_events_last_7_days}件あります"
        )
    return problems


def build_system_status(
    db: Session,
    workspace_id: UUID,
    *,
    now: datetime,
    trading_stale_after: timedelta,
    market_data_overdue_after: timedelta,
) -> SystemStatus:
    trading = _trading_worker(db, workspace_id, now, trading_stale_after)
    market = _market_data_worker(db, workspace_id, now, market_data_overdue_after)
    notifications = _notifications(db, workspace_id, now)
    bots = {
        str(state): int(count)
        for state, count in db.execute(
            select(TradingBot.actual_state, func.count())
            .where(TradingBot.workspace_id == workspace_id)
            .group_by(TradingBot.actual_state)
        ).all()
    }
    halts = {
        str(level): int(count)
        for level, count in db.execute(
            select(TradingHalt.level, func.count())
            .where(TradingHalt.workspace_id == workspace_id, TradingHalt.status == "active")
            .group_by(TradingHalt.level)
        ).all()
    }
    connections = [
        ConnectionStatus(
            id=connection.id,
            label=connection.label,
            environment=connection.environment,
            status=connection.status,
            verification_outcome=connection.verification_outcome,
            last_verified_at=connection.last_verified_at,
        )
        for connection in db.scalars(
            select(ExchangeConnection)
            .where(ExchangeConnection.workspace_id == workspace_id)
            .order_by(ExchangeConnection.label)
        ).all()
    ]
    problems = _problems(trading, market, notifications, bots, halts)
    return SystemStatus(
        checked_at=now,
        overall="attention" if problems else "ok",
        problems=problems,
        trading_worker=trading,
        market_data_worker=market,
        notifications=notifications,
        halts=halts,
        bots=bots,
        connections=connections,
    )
