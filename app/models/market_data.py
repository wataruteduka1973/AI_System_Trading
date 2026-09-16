from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.session import Base

SCHEMA = settings.database_schema


class Candle(Base):
    __tablename__ = "candle"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "open_time", name="uq_candle_business_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    timeframe: Mapped[str] = mapped_column(Text)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    high: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    low: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    close: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    trade_count: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(Text)
    quality_status: Mapped[str] = mapped_column(Text, server_default="complete")
    is_final: Mapped[bool] = mapped_column(Boolean, server_default="true")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketDataGap(Base):
    __tablename__ = "market_data_gap"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    timeframe: Mapped[str] = mapped_column(Text)
    from_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    to_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_count: Mapped[int | None]
    missing_count: Mapped[int | None]
    reason_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackfillJob(Base):
    __tablename__ = "backfill_job"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    gap_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.market_data_gap.id", ondelete="SET NULL")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    timeframe: Mapped[str] = mapped_column(Text)
    from_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    to_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL")
    )
    trigger_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default="0")
    rows_written: Mapped[int] = mapped_column(server_default="0")
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")


class MarketDataSubscription(Base):
    __tablename__ = "market_data_subscription"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "instrument_id",
            "timeframe",
            name="uq_market_data_subscription",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="CASCADE")
    )
    timeframe: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    poll_interval_seconds: Mapped[int] = mapped_column(server_default="60")
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    blocked_reason: Mapped[str | None] = mapped_column(Text)
