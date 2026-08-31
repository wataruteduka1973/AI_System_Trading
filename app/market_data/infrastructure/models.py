"""Worker-only mappings: importing these must not extend legacy API table mappings.

The existing API can still run against revision 0004 until the explicit cutover.
DDL/constraints remain owned by Alembic, not create_all/autogenerate.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WorkerBase(DeclarativeBase):
    pass


class MarketDataLease(WorkerBase):
    __tablename__ = "market_data_lease"
    __table_args__ = {"schema": "fx"}

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(primary_key=True)
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[UUID | None]
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_kind: Mapped[str | None] = mapped_column(Text)
    work_id: Mapped[UUID | None]


class WorkFields:
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID]
    instrument_id: Mapped[UUID]
    timeframe: Mapped[str] = mapped_column(Text)
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int]


class WorkerBackfill(WorkFields, WorkerBase):
    __tablename__ = "backfill_job"
    __table_args__ = {"schema": "fx"}

    from_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    to_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int]
    rows_written: Mapped[int]
    progress_report: Mapped[dict[str, object]] = mapped_column(JSONB)
    validation_result: Mapped[dict[str, object]] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerSubscription(WorkFields, WorkerBase):
    __tablename__ = "market_data_subscription"
    __table_args__ = {"schema": "fx"}

    enabled: Mapped[bool]
    poll_interval_seconds: Mapped[int]
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    scan_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
