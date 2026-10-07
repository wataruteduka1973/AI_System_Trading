"""Outbox polling + notification delivery (Horizon5 Group D / Unit 8,
docs/plans/horizon5-implementation-plan.md; wired to its first producers in
docs/plans/notification-wiring.md).

**Matching a `SystemEvent`**: `notification.event_id` is a foreign key to
`system_event.id`, not `outbox_event.id` -- the two tables have no relationship in the
schema (`outbox_event.aggregate_id` is a polymorphic pointer to whatever the event concerns).
Both carry a `correlation_id`, which is what it exists for (03_ER図とデータ定義.md): the
producer (`publish_event.publish_system_event`) writes the pair with one shared id, and this
module resolves the `SystemEvent` from it. That gives a correct `workspace_id` and `event_id`.

**At-least-once, without sending twice to someone who already got it**: an event is
`published` only once every recipient has a `sent` notification. A failed delivery keeps the
event `pending` with a later `available_at` (exponential backoff) and counts an attempt;
after `MAX_ATTEMPTS` the event is `failed`. A retry skips recipients already `sent` and
re-sends only the failed ones, reusing their `notification` row (so `delivery_attempts` counts
every try). That is the roadmap's "重複、欠落、再送をOutboxから追跡できる".

**Channels**: `adapters` maps a channel name to its adapter. A recipient on a channel with no
adapter (email while SMTP is unset) is skipped, not failed -- there is nothing to retry.
"""

import json
import logging
import smtplib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import OutboxEvent, SystemEvent
from app.models.notifications import Notification
from app.notifications.adapters.base import NotificationAdapter
from app.notifications.application.publish_event import publish_system_event

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50
MAX_ATTEMPTS = 5
GIVE_UP_EVENT_TYPE = "notification_delivery_failed"
_BACKOFF_BASE_SECONDS = 30

RecipientResolver = Callable[[Session, OutboxEvent, SystemEvent], list[tuple[str, str]]]
"""`resolver(db, outbox_event, system_event) -> [(channel, recipient_ref), ...]` -- who
should be notified about this event and over which channel(s). See
`recipients.workspace_member_recipients` for the policy in use."""


def claim_pending_outbox_events(db: Session) -> list[OutboxEvent]:
    """`SELECT ... FOR UPDATE SKIP LOCKED` so multiple Notification Worker
    processes can run concurrently without double-delivering the same event
    (SQLAlchemy 2.0: `Select.with_for_update(skip_locked=True)`, see
    https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html#selecting-for-update).
    Only rows whose `available_at` has passed: a retry is parked until its backoff is over.
    Deliberately not the market-data worker's lease/heartbeat machinery: that
    exists for long-running fetch jobs that can crash mid-flight and need
    stale-recovery, whereas a notification send is a single short call that
    either finishes or fails within one transaction."""
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending", OutboxEvent.available_at <= func.now())
        .order_by(OutboxEvent.available_at)
        .limit(_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    return list(db.scalars(statement).all())


def _announce_give_up(db: Session, event: OutboxEvent, system_event: SystemEvent) -> None:
    """Tells the workspace that a notification could not be delivered after every retry, as a new
    event: the original never reached everyone, and without this nobody would know. It is read in
    the application (`in_app` always works), and the email that failed may fail again.

    Never for a give-up event itself: its failing would announce itself again, forever."""
    if event.event_type == GIVE_UP_EVENT_TYPE:
        return
    failed_channels = sorted(
        {
            channel
            for (channel,) in db.execute(
                select(Notification.channel).where(
                    Notification.event_id == system_event.id, Notification.status == "failed"
                )
            ).all()
        }
    )
    publish_system_event(
        db,
        workspace_id=system_event.workspace_id,
        severity="error",
        category="notification",
        event_type=GIVE_UP_EVENT_TYPE,
        reason_code="delivery_failed",
        message=(
            f"通知を届けられませんでした(「{system_event.message}」、"
            f"届かなかった経路: {', '.join(failed_channels) or '不明'})"
        ),
        payload={
            "original_event_type": system_event.event_type,
            "original_event_id": str(system_event.id),
            "failed_channels": failed_channels,
            "attempts": event.attempts,
        },
        aggregate_type="outbox_event",
        aggregate_id=event.id,
        source_type="notification_worker",
    )


def _subject_and_body(system_event: SystemEvent) -> tuple[str, str]:
    subject = f"[{system_event.severity.upper()}] {system_event.message}"
    details = json.dumps(system_event.payload, ensure_ascii=False, indent=2, default=str)
    return subject, f"{system_event.message}\n\n{details}\n"


def deliver_pending_notifications(
    db: Session,
    *,
    adapters: Mapping[str, NotificationAdapter],
    recipient_resolver: RecipientResolver,
) -> int:
    """Claims a batch of pending `OutboxEvent` rows and delivers each to every recipient
    `recipient_resolver` names. Returns the number of outbox events processed (not the
    number of notifications sent -- an event with zero resolved recipients still counts as
    processed)."""
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

        subject, body = _subject_and_body(system_event)
        any_failed = False
        for channel, recipient_ref in recipient_resolver(db, event, system_event):
            adapter = adapters.get(channel)
            if adapter is None:
                continue
            notification = db.scalar(
                select(Notification).where(
                    Notification.event_id == system_event.id,
                    Notification.channel == channel,
                    Notification.recipient_ref == recipient_ref,
                )
            )
            if notification is not None and notification.status in ("sent", "acknowledged"):
                continue
            if notification is None:
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
                adapter.send(recipient=recipient_ref, subject=subject, body=body)
            except (OSError, smtplib.SMTPException):
                # OSError alone only catches connection-level failures (DNS
                # unreachable, connection refused). smtplib.SMTPException's
                # subclasses (auth failure, recipient refused -- the SMTP
                # protocol-level failures most likely in real operation)
                # inherit directly from Exception, not OSError, and would
                # otherwise propagate uncaught and crash the whole batch.
                notification.status = "failed"
                notification.delivery_attempts += 1
                any_failed = True
            else:
                notification.status = "sent"
                notification.sent_at = datetime.now(UTC)

        if any_failed:
            event.attempts += 1
            if event.attempts >= MAX_ATTEMPTS:
                event.status = "failed"
                logger.error("notification.deliver: giving up on outbox event %s", event.id)
                _announce_give_up(db, event, system_event)
            else:
                delay = _BACKOFF_BASE_SECONDS * 2**event.attempts
                event.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        else:
            event.status = "published"
            event.published_at = datetime.now(UTC)
        db.flush()
        processed += 1
    db.commit()
    return processed
