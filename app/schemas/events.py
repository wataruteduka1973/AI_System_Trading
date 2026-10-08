from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.base import OrmModel


class SystemEventRead(OrmModel):
    id: UUID
    occurred_at: datetime
    severity: str
    category: str
    event_type: str
    reason_code: str | None
    source_type: str
    source_id: UUID | None
    target_type: str | None
    target_id: UUID | None
    correlation_id: UUID
    message: str
    payload: dict[str, object]


class EventPageRead(BaseModel):
    items: list[SystemEventRead]
    next_before: datetime | None
    next_before_id: UUID | None
    """Pass both back as `before` and `before_id` for the next (older) page; `None` at the end."""


class EventFacetsRead(BaseModel):
    severities: list[str]
    categories: list[str]
    event_types: list[str]
    reason_codes: list[str]
