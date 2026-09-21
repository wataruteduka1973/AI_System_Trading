from datetime import datetime
from uuid import UUID

from app.schemas.base import OrmModel


class TradingHaltRead(OrmModel):
    id: UUID
    scope_type: str
    scope_id: UUID | None
    level: str
    reason_code: str
    status: str
    halted_at: datetime
    released_at: datetime | None
