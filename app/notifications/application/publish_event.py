"""The producer side of the outbox: record a domain event so the Notification Worker can
tell people about it (docs/plans/notification-wiring.md).

A `SystemEvent` (what happened, scoped to a workspace) and an `OutboxEvent` (the pending
delivery) are written together with one shared `correlation_id`, which is how
`deliver_notifications` finds the `SystemEvent` again. Nothing here commits: the event must
commit or roll back with the change that caused it (a halt that rolls back must not have
been announced), so the caller's transaction owns it.

`payload` goes to the audit trail and into notification bodies: only identifiers, levels and
counts -- never credentials or raw exchange responses (`contains_sensitive_data` stays false,
which a DB constraint enforces).
"""

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.audit import OutboxEvent, SystemEvent


def publish_system_event(
    db: Session,
    *,
    workspace_id: UUID,
    severity: str,
    category: str,
    event_type: str,
    reason_code: str | None,
    message: str,
    payload: dict[str, object],
    aggregate_type: str,
    aggregate_id: UUID,
    source_type: str,
    source_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    correlation_id: UUID | None = None,
) -> SystemEvent:
    correlation_id = correlation_id or uuid4()
    system_event = SystemEvent(
        workspace_id=workspace_id,
        severity=severity,
        category=category,
        event_type=event_type,
        reason_code=reason_code,
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id,
        message=message,
        payload=payload,
    )
    db.add(system_event)
    db.add(
        OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
    )
    db.flush()
    return system_event
