"""Server-side detection of a stopped trading worker (docs/plans/worker-failure-handling.md).

The trading worker stamps `bot_run.heartbeat_at` for every active bot on every pass
(`bot_execution_loop`), whether the evaluation worked or not. If the worker dies, hangs, or was
never started, those stamps stop moving. This check -- run by the *notification* worker, a
separate process, so it can still speak when the trading worker cannot -- finds active bots whose
stamp is older than `stale_after` and raises one `trading_worker_stalled` event per workspace
per outage.

One alert per outage, not per check: an alert is skipped while every stale bot's last stamp is
older than the last alert (it was already reported). A bot that recovers and stalls again has a
newer stamp, so it is reported again. There is no "recovered" notice.

This cannot see the case where the notification worker is down too (the whole machine off): that
needs monitoring from outside, which this application does not provide.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import SystemEvent
from app.models.strategy import BotRun, TradingBot
from app.notifications.application.publish_event import publish_system_event

EVENT_TYPE = "trading_worker_stalled"


def find_stalled_bots(
    db: Session, *, now: datetime, stale_after: timedelta, workspace_id: UUID | None = None
) -> list[tuple[UUID, str, datetime]]:
    """`(workspace_id, bot name, last sign of life)` for each active bot whose heartbeat is older
    than `stale_after`, oldest first; a bot that never stamped counts from its start. Shared by
    the alert below and the status API (`monitoring.system_status`)."""
    last_seen = func.coalesce(BotRun.heartbeat_at, BotRun.started_at)
    statement = (
        select(TradingBot.workspace_id, TradingBot.name, last_seen)
        .join(BotRun, BotRun.bot_id == TradingBot.id)
        .where(
            TradingBot.actual_state.in_(("running", "paused")),
            BotRun.status.in_(("running", "paused")),
            last_seen < now - stale_after,
        )
        .order_by(last_seen)
    )
    if workspace_id is not None:
        statement = statement.where(TradingBot.workspace_id == workspace_id)
    return [(row[0], row[1], row[2]) for row in db.execute(statement).all()]


def check_trading_worker(db: Session, *, now: datetime, stale_after: timedelta) -> int:
    """Raises the alert for each workspace with a stalled active bot that has not been
    reported yet, and commits. Returns how many workspaces were alerted."""
    rows = find_stalled_bots(db, now=now, stale_after=stale_after)
    by_workspace: dict[UUID, list[tuple[str, datetime]]] = {}
    for workspace_id, name, seen in rows:
        by_workspace.setdefault(workspace_id, []).append((name, seen))

    alerted = 0
    for workspace_id, stalled in by_workspace.items():
        last_alert = db.scalar(
            select(func.max(SystemEvent.occurred_at)).where(
                SystemEvent.workspace_id == workspace_id, SystemEvent.event_type == EVENT_TYPE
            )
        )
        if last_alert is not None and all(seen <= last_alert for _, seen in stalled):
            continue  # the same outage, already reported
        oldest = min(seen for _, seen in stalled)
        minutes = int((now - oldest).total_seconds() // 60)
        names = [name for name, _ in stalled]
        publish_system_event(
            db,
            workspace_id=workspace_id,
            severity="error",
            category="system",
            event_type=EVENT_TYPE,
            reason_code="worker_stalled",
            message=(
                f"トレーディングWorkerが止まっている可能性があります"
                f"(最後の確認から{minutes}分、対象Bot: {', '.join(names[:5])}"
                f"{' ほか' if len(names) > 5 else ''})"
            ),
            payload={
                "bot_names": names,
                "oldest_heartbeat": oldest.isoformat(),
                "stale_after_seconds": int(stale_after.total_seconds()),
            },
            aggregate_type="workspace",
            aggregate_id=workspace_id,
            source_type="watchdog",
        )
        alerted += 1
    if alerted:
        db.commit()
    return alerted
