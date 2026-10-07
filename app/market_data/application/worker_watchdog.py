"""Server-side detection of a stopped market-data worker (docs/plans/notification-sources.md).

The worker takes every enabled, unblocked subscription when its `next_run_at` passes and pushes
`next_run_at` forward again. If `next_run_at` is long past, nothing is processing it: the worker
is down, hung or was never started. That is how the market-data worker went unnoticed for two
weeks in September 2026 (see `scripts/start_local.py`), and why this check exists.

Run by the notification worker, a separate process. One alert per workspace per outage: it is
skipped while every overdue subscription's `next_run_at` is older than the last alert (the same
outage, already reported); a subscription that was served and then overdue again has a newer
`next_run_at` and is reported again. A blocked subscription is not overdue -- it is already
announced when it blocks (`stop_notice`).

It cannot see the case where the notification worker is down too; see `trading`'s watchdog.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import SystemEvent
from app.models.instruments import Instrument
from app.models.market_data import MarketDataSubscription
from app.notifications.application.publish_event import publish_system_event

EVENT_TYPE = "market_data_worker_stalled"


def check_market_data_worker(db: Session, *, now: datetime, overdue_after: timedelta) -> int:
    """Raises the alert for each workspace with an overdue subscription that has not been
    reported yet, and commits. Returns how many workspaces were alerted."""
    rows = db.execute(
        select(
            MarketDataSubscription.workspace_id,
            Instrument.symbol,
            MarketDataSubscription.timeframe,
            MarketDataSubscription.next_run_at,
        )
        .join(Instrument, Instrument.id == MarketDataSubscription.instrument_id)
        .where(
            MarketDataSubscription.enabled.is_(True),
            MarketDataSubscription.blocked_reason.is_(None),
            MarketDataSubscription.next_run_at < now - overdue_after,
        )
        .order_by(MarketDataSubscription.next_run_at)
    ).all()
    by_workspace: dict[UUID, list[tuple[str, datetime]]] = {}
    for workspace_id, symbol, timeframe, due in rows:
        by_workspace.setdefault(workspace_id, []).append((f"{symbol} {timeframe}", due))

    alerted = 0
    for workspace_id, overdue in by_workspace.items():
        last_alert = db.scalar(
            select(func.max(SystemEvent.occurred_at)).where(
                SystemEvent.workspace_id == workspace_id, SystemEvent.event_type == EVENT_TYPE
            )
        )
        if last_alert is not None and all(due <= last_alert for _, due in overdue):
            continue  # the same outage, already reported
        oldest = min(due for _, due in overdue)
        minutes = int((now - oldest).total_seconds() // 60)
        labels = [label for label, _ in overdue]
        publish_system_event(
            db,
            workspace_id=workspace_id,
            severity="error",
            category="market_data",
            event_type=EVENT_TYPE,
            reason_code="worker_stalled",
            message=(
                f"市場データWorkerが止まっている可能性があります"
                f"(取得が{minutes}分遅れ、対象: {', '.join(labels[:5])}"
                f"{' ほか' if len(labels) > 5 else ''})"
            ),
            payload={
                "overdue": labels,
                "oldest_due": oldest.isoformat(),
                "overdue_after_seconds": int(overdue_after.total_seconds()),
            },
            aggregate_type="workspace",
            aggregate_id=workspace_id,
            source_type="watchdog",
        )
        alerted += 1
    if alerted:
        db.commit()
    return alerted
