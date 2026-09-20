"""Strategy/risk governance and per-candle decision records.

Maps tables already created by `alembic/versions/20260816_0001_initial_schema.py`
(via `database/postgresql_schema_v0.1.sql`, unchanged since 2026-08-16) -- this
module does not create any new tables or columns, it only adds the ORM layer
that was missing for them. See the Paper Trading data-model PR summary for the
full comparison against `docs/concept/FXtrading_rebuild/03_ER図とデータ定義.md`
and `05_アーキテクチャと移行計画.md`, including known gaps not resolved here:

- `StrategyVersion.lifecycle_status`'s DB CHECK constraint omits `deployed`,
  even though `08_取引アルゴリズムとリスク初期値.md`§3 documents it as part of
  the lifecycle.
- `TradingHalt.status` (active/release_pending/released) is a separate state
  machine from `level`; only `level`'s transitions were documented so far in
  `05_アーキテクチャと移行計画.md`'s "停止レベルの状態遷移表".

`Strategy`, `RiskProfile`, `BotRun`, and `TradingBot` are included here even
though they were not in the original scope list: SQLAlchemy resolves a
`ForeignKey("schema.table.id")` string against every table registered in the
shared `Base.metadata`, so a target table with no mapped class anywhere
raises `NoReferencedTableError` the first time *any* row is flushed --
verified directly against the live DB during this change (see PR summary).
`StrategyVersion.strategy_id`, `RiskProfileVersion.risk_profile_id`, and
`Signal.bot_run_id` (via `BotRun.bot_id` -> `TradingBot`) needed these to
resolve. `AccountSelectionPolicy` (referenced by `TradingAccount`, see
`app/models/connections.py`) and `SystemEvent` (referenced by `TradingHalt`,
see `app/models/audit.py`) were added the same way, in the modules matching
their own grouping in `03_ER図とデータ定義.md`.

`Signal.model_artifact_id` deliberately keeps a bare UUID column with no
`ForeignKey` object: resolving it the same way would additionally require
mapping `model_artifact`/`model_candidate`/`training_run`/`dataset_snapshot`/
`model_source` -- the Horizon 6 "AI Model Lab" table cluster, which multiple
prior audits in this conversation confirmed is deliberately gated (no
approved entry conditions yet). Mapping that cluster now, even as inert
column definitions, would pre-empt that gate. `TradingBot`/`BotRun` do not
have this problem -- their own foreign keys resolve entirely within
already-in-scope tables (workspace/exchange_connection/trading_account/
instrument/strategy_version/risk_profile_version).

**Update (Horizon4-lite, see `docs/decisions/0003-horizon4-lite-backtest-before-chronos.md`)**:
`DatasetSnapshot` -- one member of the above Horizon 6 cluster -- is now mapped
in `app/models/backtest.py`, as a narrow ADR-recorded exception:
`backtest_run.dataset_snapshot_id` is a NOT NULL foreign key, so a `BacktestRun`
cannot exist without it. `model_artifact`/`model_candidate`/`training_run`/
`model_source` remain unmapped; this does not reopen the Horizon 6 gate.
`StrategyVersion.backtest_runs`/`RiskProfileVersion.backtest_runs` below are
the corresponding bidirectional `relationship()`s to `backtest.py`'s
`BacktestRun`.

`relationship()` (2026-09-20 addition): unlike `market_data.py`/`instruments.py`,
this module follows `workspace.py`/`connections.py`'s convention of declaring
bidirectional `relationship()`s, since this module's tables form a genuinely
interconnected graph (signal -> risk_decision -> order_intent -> ...) that a
follow-up task will build business logic against. Relationships are only
declared between classes that are themselves in scope for the Paper Trading
data model (this module, `app/models/trading.py`, plus the two supporting
classes `AccountSelectionPolicy` and `SystemEvent`); FKs to out-of-scope
tables (`workspace`, `app_user`, `exchange_connection`, `instrument`,
`candle`) are deliberately left as plain columns with no `relationship()`,
to avoid unrelated changes to those tables' own modules. `TradingHalt.scope_id`
has no `relationship()` for the same reason as before: it is polymorphic
(paired with `scope_type`) and the DB itself has no foreign key on it.

No `cascade` argument is passed anywhere in this module (matching
`workspace.py`/`connections.py`, which also never do): the default SQLAlchemy
cascade (`save-update, merge`) does not delete children when a parent is
deleted, and the DB-level `ondelete=RESTRICT` on most of these FKs already
blocks parent deletion outright while children exist. This matters most for
`Fill`/`LedgerEntry` (append-only; see `app/models/trading.py`'s docstring)
even though those classes live in the other module.

Cross-module relationship type hints (to `app.models.trading`,
`app.models.connections`, `app.models.audit`) are declared under
`TYPE_CHECKING` and referenced as quoted strings, never via a real import at
module load time. This module's tables reference `trading.py`'s in both
directions (`TradingBot.account_id -> TradingAccount` and
`OrderIntent.signal_id`/`risk_decision_id -> Signal`/`RiskDecision`), so
picking either module to hold a real import of the other would be an
arbitrary, fragile choice; `TYPE_CHECKING`-only imports on both sides avoid
that entirely while still giving mypy full type information.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
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
    from app.models.audit import SystemEvent
    from app.models.backtest import BacktestRun
    from app.models.trading import OrderIntent, TradingAccount

SCHEMA = settings.database_schema


class Strategy(Base):
    __tablename__ = "strategy"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_strategy_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    strategy_versions: Mapped[list["StrategyVersion"]] = relationship(back_populates="strategy")


class StrategyVersion(Base):
    __tablename__ = "strategy_version"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
        UniqueConstraint("strategy_id", "checksum", name="uq_strategy_version_checksum"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.strategy.id", ondelete="CASCADE")
    )
    version: Mapped[int]
    supported_market_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    checksum: Mapped[str] = mapped_column(String(64))
    lifecycle_status: Mapped[str] = mapped_column(Text, server_default="draft")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    strategy: Mapped["Strategy"] = relationship(back_populates="strategy_versions")
    signals: Mapped[list["Signal"]] = relationship(back_populates="strategy_version")
    trading_bots: Mapped[list["TradingBot"]] = relationship(back_populates="strategy_version")
    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="strategy_version")


class RiskProfile(Base):
    __tablename__ = "risk_profile"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_risk_profile_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    risk_profile_versions: Mapped[list["RiskProfileVersion"]] = relationship(
        back_populates="risk_profile"
    )


class RiskProfileVersion(Base):
    __tablename__ = "risk_profile_version"
    __table_args__ = (
        UniqueConstraint("risk_profile_id", "version", name="uq_risk_profile_version"),
        UniqueConstraint("risk_profile_id", "checksum", name="uq_risk_profile_version_checksum"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    risk_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.risk_profile.id", ondelete="CASCADE")
    )
    version: Mapped[int]
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    template_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    risk_profile: Mapped["RiskProfile"] = relationship(back_populates="risk_profile_versions")
    risk_decisions: Mapped[list["RiskDecision"]] = relationship(
        back_populates="risk_profile_version"
    )
    trading_bots: Mapped[list["TradingBot"]] = relationship(back_populates="risk_profile_version")
    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="risk_profile_version")


class TradingBot(Base):
    __tablename__ = "trading_bot"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_trading_bot_name"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    execution_mode: Mapped[str] = mapped_column(Text)
    strategy_mode: Mapped[str] = mapped_column(Text)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.exchange_connection.id", ondelete="RESTRICT")
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trading_account.id", ondelete="RESTRICT")
    )
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    timeframe: Mapped[str] = mapped_column(Text)
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.strategy_version.id", ondelete="RESTRICT")
    )
    risk_profile_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.risk_profile_version.id", ondelete="RESTRICT")
    )
    desired_state: Mapped[str] = mapped_column(Text, server_default="stopped")
    actual_state: Mapped[str] = mapped_column(Text, server_default="stopped")
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["TradingAccount"] = relationship(back_populates="trading_bots")
    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="trading_bots")
    risk_profile_version: Mapped["RiskProfileVersion"] = relationship(back_populates="trading_bots")
    bot_runs: Mapped[list["BotRun"]] = relationship(back_populates="bot")


class BotRun(Base):
    __tablename__ = "bot_run"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    bot_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.trading_bot.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, server_default="starting")
    code_version: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    bot: Mapped["TradingBot"] = relationship(back_populates="bot_runs")
    signals: Mapped[list["Signal"]] = relationship(back_populates="bot_run")


class Signal(Base):
    __tablename__ = "signal"
    __table_args__ = (
        UniqueConstraint(
            "bot_run_id",
            "candle_id",
            "strategy_version_id",
            "input_checksum",
            name="uq_signal_idempotency",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    bot_run_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.bot_run.id", ondelete="CASCADE"))
    candle_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.candle.id", ondelete="RESTRICT"))
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.strategy_version.id", ondelete="RESTRICT")
    )
    # No ForeignKey object: see module docstring (Horizon 6 model_artifact
    # cluster deliberately not mapped). The DB-level FK/RESTRICT still exists
    # and is enforced; only the ORM-side declaration is omitted.
    model_artifact_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    rationale: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_checksum: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[UUID] = mapped_column(server_default=func.gen_random_uuid())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bot_run: Mapped["BotRun"] = relationship(back_populates="signals")
    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="signals")
    risk_decisions: Mapped[list["RiskDecision"]] = relationship(back_populates="signal")
    order_intent: Mapped["OrderIntent | None"] = relationship(
        back_populates="signal", uselist=False
    )


class RiskDecision(Base):
    __tablename__ = "risk_decision"
    __table_args__ = (
        UniqueConstraint("signal_id", "risk_profile_version_id", name="uq_risk_decision_signal"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    signal_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.signal.id", ondelete="CASCADE"))
    risk_profile_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.risk_profile_version.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str] = mapped_column(Text)
    rule_results: Mapped[dict[str, Any]] = mapped_column(JSONB)
    adjusted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    reason_code: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    signal: Mapped["Signal"] = relationship(back_populates="risk_decisions")
    risk_profile_version: Mapped["RiskProfileVersion"] = relationship(
        back_populates="risk_decisions"
    )
    order_intent: Mapped["OrderIntent | None"] = relationship(
        back_populates="risk_decision", uselist=False
    )


class TradingHalt(Base):
    """`scope_id` is polymorphic (its meaning depends on `scope_type`: NULL
    for `system`/`workspace`, otherwise a connection/account/bot/instrument
    id) and therefore intentionally has no foreign key, matching the DB."""

    __tablename__ = "trading_halt"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    scope_type: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[UUID | None]
    level: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(Text)
    trigger_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.system_event.id", ondelete="SET NULL")
    )
    auto_releasable: Mapped[bool] = mapped_column(Boolean, server_default="false")
    release_condition: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    halted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL")
    )

    trigger_event: Mapped["SystemEvent | None"] = relationship(back_populates="triggered_halts")
