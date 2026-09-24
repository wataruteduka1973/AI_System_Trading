"""Outbox polling + notification delivery (Horizon5 Group D / Unit 8,
docs/plans/horizon5-implementation-plan.md). **Skeleton**: no domain event
producer writes to `outbox_event` yet -- ADR 0004-style trading_halt
notifications, connection-credential-change alerts, etc. are all still
unwired. Wiring an actual domain event source is a separate task (plan §Unit
8 "想定リスク"); this module gives that future task somewhere to plug in.

**Deviation from the plan's illustrative code, with reason**: the plan's own
example sets `Notification.event_id = event.id` where `event` is the claimed
`OutboxEvent`, and `Notification.workspace_id = event.aggregate_id`. Both are
wrong by construction and the plan flags the function itself as "a skeleton,
do not use as-is": `notification.event_id` is a foreign key to
`system_event.id`, not `outbox_event.id` -- there is no relationship between
those two tables in the schema at all (`outbox_event.aggregate_id` is a
polymorphic pointer to whatever entity the event concerns, e.g. a
`trading_bot.id`, not a `system_event` row) -- so inserting it verbatim would
either violate `notification`'s foreign key constraint or silently write the
wrong workspace. Both tables do carry a `correlation_id`, which is exactly
what that column exists for (03_ER図とデータ定義.md), so this version resolves
the matching `SystemEvent` by `correlation_id` instead: that gives a correct
`workspace_id` (`system_event.workspace_id` is NOT NULL) and a correct
`event_id` in one step, and keeps `recipient_resolver` focused on the one
thing the plan actually calls a product decision out of this Unit's scope --
who to notify -- rather than also asking it to invent a workspace id.
"""

import smtplib
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import OutboxEvent, SystemEvent
from app.models.notifications import Notification
from app.notifications.adapters.base import NotificationAdapter

_BATCH_SIZE = 50

RecipientResolver = Callable[[OutboxEvent, SystemEvent], list[tuple[str, str]]]
"""`resolver(outbox_event, system_event) -> [(channel, recipient_ref), ...]` --
who should be notified about this event and over which channel(s). Left as an
injected callback since the actual workspace/user targeting policy is a
product decision out of this Unit's scope (plan §Unit 8)."""


def claim_pending_outbox_events(db: Session) -> list[OutboxEvent]:
    """`SELECT ... FOR UPDATE SKIP LOCKED` so multiple Notification Worker
    processes can run concurrently without double-delivering the same event
    (SQLAlchemy 2.0: `Select.with_for_update(skip_locked=True)`, see
    https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html#selecting-for-update).
    Deliberately not the market-data worker's lease/heartbeat machinery: that
    exists for long-running fetch jobs that can crash mid-flight and need
    stale-recovery, whereas a notification send is a single short call that
    either finishes or fails within one transaction."""
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending")
        .order_by(OutboxEvent.available_at)
        .limit(_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    return list(db.scalars(statement).all())


def deliver_pending_notifications(
    db: Session, *, adapter: NotificationAdapter, recipient_resolver: RecipientResolver
) -> int:
    """Claims a batch of pending `OutboxEvent` rows and attempts delivery of
    each recipient `recipient_resolver` names. Returns the number of outbox
    events processed (not the number of notifications sent -- an event with
    zero resolved recipients still counts as processed)."""
    processed = 0
    for event in claim_pending_outbox_events(db):
        system_event = db.scalar(
            select(SystemEvent).where(SystemEvent.correlation_id == event.correlation_id)
        )
        if system_event is None:
            # No matching SystemEvent for this outbox row's correlation_id --
            # nothing to attribute a Notification to (workspace_id, event_id
            # both come from it). Mark failed rather than leaving it
            # perpetually "pending" for the next poll to retry forever.
            event.status = "failed"
            event.attempts += 1
            db.flush()
            continue

        for channel, recipient_ref in recipient_resolver(event, system_event):
            notification = Notification(
                workspace_id=system_event.workspace_id,
                event_id=system_event.id,
                channel=channel,
                recipient_ref=recipient_ref,
                status="queued",
                # `server_default="0"` only applies on INSERT -- a freshly
                # constructed object has `delivery_attempts=None` in Python
                # until then, and the `+= 1` below runs before that INSERT.
                delivery_attempts=0,
            )
            db.add(notification)
            try:
                adapter.send(
                    recipient=recipient_ref, subject=event.event_type, body=str(event.payload)
                )
                notification.status = "sent"
            except (OSError, smtplib.SMTPException):
                # OSError alone only catches connection-level failures (DNS
                # unreachable, connection refused). smtplib.SMTPException's
                # subclasses (auth failure, recipient refused -- the SMTP
                # protocol-level failures most likely in real operation)
                # inherit directly from Exception, not OSError, and would
                # otherwise propagate uncaught and crash the whole batch.
                notification.status = "failed"
                notification.delivery_attempts += 1
        event.status = "published"
        db.flush()
        processed += 1
    db.commit()
    return processed
