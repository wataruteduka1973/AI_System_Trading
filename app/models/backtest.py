"""Backtest data model (DatasetSnapshot, BacktestRun, BacktestTrade).

Maps tables already created by `alembic/versions/20260816_0001_initial_schema.py`
(via `database/postgresql_schema_v0.1.sql`, unchanged since 2026-08-16) -- this
module does not create any new tables or columns, it only adds the ORM layer
that was missing for them.

**Scope note (narrow exception to an existing gate)**: `app/models/strategy.py`'s
module docstring documents that the Horizon 6 "AI Model Lab" table cluster
(`model_artifact`/`model_candidate`/`training_run`/`dataset_snapshot`/
`model_source`) was deliberately left unmapped, since Horizon 6 has no approved
entry conditions yet. `DatasetSnapshot` is mapped here anyway, as a narrow,
ADR-recorded exception: `docs/decisions/0003-horizon4-lite-backtest-before-chronos.md`
approves building a minimal Backtest capability (`backtest_run`/`backtest_trade`)
ahead of Horizon 6, and `backtest_run.dataset_snapshot_id` is a NOT NULL foreign
key -- a `BacktestRun` cannot exist without a corresponding `DatasetSnapshot` row.
The rest of the Horizon 6 cluster (`model_artifact`, `model_candidate`,
`training_run`, `model_source`) remains deliberately unmapped; this module does
not reopen that gate. See `docs/plans/horizon4-lite-backtest.md` for the full
Horizon4-lite scope.

`metadata_` (on `BacktestTrade`) maps the DB column literally named `metadata`,
renamed on the Python side because `metadata` is reserved on every SQLAlchemy
declarative class (it is `Base.metadata`, the `MetaData` registry).

`relationship()`s follow `strategy.py`/`trading.py`'s convention of declaring
them bidirectionally between in-scope classes. `StrategyVersion.backtest_runs`
and `RiskProfileVersion.backtest_runs` are added in `strategy.py` alongside this
module's change, matching that module's own documented convention of keeping
both sides of a relationship declared where the classes live.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.strategy import RiskProfileVersion, StrategyVersion

SCHEMA = settings.database_schema


class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshot"
    __table_args__ = (
        UniqueConstraint("workspace_id", "checksum", name="uq_dataset_snapshot_checksum"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    instruments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    intervals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    from_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    to_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(BigInteger)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="dataset_snapshot")


class BacktestRun(Base):
    __tablename__ = "backtest_run"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.strategy_version.id", ondelete="RESTRICT")
    )
    risk_profile_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.risk_profile_version.id", ondelete="RESTRICT")
    )
    dataset_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_snapshot.id", ondelete="RESTRICT")
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    code_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    summary_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dataset_snapshot: Mapped["DatasetSnapshot"] = relationship(back_populates="backtest_runs")
    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="backtest_runs")
    risk_profile_version: Mapped["RiskProfileVersion"] = relationship(
        back_populates="backtest_runs"
    )
    trades: Mapped[list["BacktestTrade"]] = relationship(back_populates="backtest_run")


class BacktestTrade(Base):
    __tablename__ = "backtest_trade"
    __table_args__ = (
        UniqueConstraint("backtest_run_id", "sequence_no", name="uq_backtest_trade_sequence"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    backtest_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.backtest_run.id", ondelete="CASCADE")
    )
    sequence_no: Mapped[int]
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    side: Mapped[str] = mapped_column(Text)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    fees: Mapped[Decimal] = mapped_column(Numeric(38, 18), server_default="0")
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")

    backtest_run: Mapped["BacktestRun"] = relationship(back_populates="trades")
