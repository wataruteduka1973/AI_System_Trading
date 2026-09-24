from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.strategy import TradingHalt

SCHEMA = settings.database_schema


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.app_user.id"))
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[UUID | None]
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    correlation_id: Mapped[UUID]
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SystemEvent(Base):
    """Distinct from `AuditLog`: audit records prove *who changed what*;
    this records *what happened in the system* (03_ER図とデータ定義.md).
    Added for `TradingHalt.trigger_event_id` to resolve (see
    `app/models/strategy.py` module docstring); already existed in the live
    DB. `source_id`/`target_id` are polymorphic (paired with `source_type`/
    `target_type`), matching the DB's own lack of a foreign key on them."""

    __tablename__ = "system_event"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    severity: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[UUID | None]
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[UUID | None]
    correlation_id: Mapped[UUID]
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    contains_sensitive_data: Mapped[bool] = mapped_column(Boolean, server_default="false")

    triggered_halts: Mapped[list["TradingHalt"]] = relationship(back_populates="trigger_event")


class OutboxEvent(Base):
    """Horizon5 Group D / Unit 8 (docs/plans/horizon5-implementation-plan.md).
    Generic transactional-outbox row: a domain event awaiting publication, not
    scoped to a workspace or any particular entity type itself -- `aggregate_type`/
    `aggregate_id` are a polymorphic pointer to whatever the event concerns (a
    `trading_bot.id`, `trading_account.id`, etc.), matching the DB's own lack of a
    foreign key on them (same reasoning as `SystemEvent.source_id`/`target_id`
    above). No domain event producer writes to this table yet -- see
    `app/notifications/application/deliver_notifications.py`'s module docstring."""

    __tablename__ = "outbox_event"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    aggregate_type: Mapped[str] = mapped_column(Text)
    aggregate_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
