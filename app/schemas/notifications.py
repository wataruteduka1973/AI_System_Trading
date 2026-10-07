from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.base import OrmModel


class NotificationRead(OrmModel):
    id: UUID
    event_id: UUID
    severity: str
    category: str
    event_type: str
    message: str
    payload: dict[str, object]
    occurred_at: datetime
    status: str
    """`sent` until the person acknowledges it, then `acknowledged`."""
    acknowledged_at: datetime | None


class NotificationListRead(BaseModel):
    unacknowledged_count: int
    """Over all of the person's notifications in the workspace, not just `items`."""
    items: list[NotificationRead]


class AcknowledgeAllRead(BaseModel):
    acknowledged: int
