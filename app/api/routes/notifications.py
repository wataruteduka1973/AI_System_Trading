"""The signed-in person's in-app notifications (docs/plans/notification-wiring.md): what the
Notification Worker recorded for them, newest first, and acknowledging them.

Strictly one's own: a notification is addressed to a user id (`recipient_ref`), and someone
else's -- even a workspace colleague's -- is a 404, as if it did not exist. Every member
may read their own (`Viewer`); in practice only Owners and Operators are sent any
(`recipients.NOTIFIED_ROLES`).
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.audit import SystemEvent
from app.models.notifications import Notification
from app.models.workspace import AppUser
from app.schemas.notifications import (
    AcknowledgeAllRead,
    NotificationListRead,
    NotificationRead,
)
from app.security.rbac import require_viewer_role

router = APIRouter(prefix="/workspaces/{workspace_id}/notifications", tags=["notifications"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]

_VISIBLE_STATUSES = ("sent", "acknowledged")


def _own(workspace_id: UUID, user: AppUser) -> tuple:
    return (
        Notification.workspace_id == workspace_id,
        Notification.channel == "in_app",
        Notification.recipient_ref == str(user.id),
        Notification.status.in_(_VISIBLE_STATUSES),
    )


def _read(notification: Notification, event: SystemEvent) -> NotificationRead:
    return NotificationRead(
        id=notification.id,
        event_id=event.id,
        severity=event.severity,
        category=event.category,
        event_type=event.event_type,
        message=event.message,
        payload=event.payload,
        occurred_at=event.occurred_at,
        status=notification.status,
        acknowledged_at=notification.acknowledged_at,
    )


@router.get("", response_model=NotificationListRead)
def list_notifications(
    workspace_id: UUID,
    db: DatabaseSession,
    user: Viewer,
    unacknowledged_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationListRead:
    statement = (
        select(Notification, SystemEvent)
        .join(SystemEvent, SystemEvent.id == Notification.event_id)
        .where(*_own(workspace_id, user))
        .order_by(SystemEvent.occurred_at.desc(), Notification.id)
        .limit(limit)
    )
    if unacknowledged_only:
        statement = statement.where(Notification.status == "sent")
    unacknowledged_count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(*_own(workspace_id, user), Notification.status == "sent")
    )
    return NotificationListRead(
        unacknowledged_count=unacknowledged_count or 0,
        items=[_read(notification, event) for notification, event in db.execute(statement).all()],
    )


@router.post("/acknowledge-all", response_model=AcknowledgeAllRead)
def acknowledge_all_notifications(
    workspace_id: UUID, db: DatabaseSession, user: Viewer
) -> AcknowledgeAllRead:
    result = db.execute(
        update(Notification)
        .where(*_own(workspace_id, user), Notification.status == "sent")
        .values(status="acknowledged", acknowledged_at=datetime.now(UTC), acknowledged_by=user.id)
        .returning(Notification.id)
    )
    acknowledged = len(result.all())
    db.commit()
    return AcknowledgeAllRead(acknowledged=acknowledged)


@router.post("/{notification_id}/acknowledge", response_model=NotificationRead)
def acknowledge_notification(
    workspace_id: UUID, notification_id: UUID, db: DatabaseSession, user: Viewer
) -> NotificationRead:
    """Idempotent: acknowledging again changes nothing (the first time and person stay)."""
    row = db.execute(
        select(Notification, SystemEvent)
        .join(SystemEvent, SystemEvent.id == Notification.event_id)
        .where(Notification.id == notification_id, *_own(workspace_id, user))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    notification, event = row
    if notification.status != "acknowledged":
        notification.status = "acknowledged"
        notification.acknowledged_at = datetime.now(UTC)
        notification.acknowledged_by = user.id
        db.commit()
    return _read(notification, event)
