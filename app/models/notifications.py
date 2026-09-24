from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.session import Base

SCHEMA = settings.database_schema


class Notification(Base):
    """Horizon5 Group D / Unit 8 (docs/plans/horizon5-implementation-plan.md).
    One delivery attempt of a `SystemEvent` to one recipient over one channel --
    unlike `OutboxEvent` (which is workspace-agnostic), this row is always
    workspace-scoped, since `system_event.workspace_id` is NOT NULL and every
    `Notification.event_id` points at one."""

    __tablename__ = "notification"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.system_event.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    recipient_ref: Mapped[str] = mapped_column(Text)
    delivery_attempts: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.app_user.id"))
