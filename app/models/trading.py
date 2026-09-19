"""Paper/live account, order lifecycle, and append-only ledger records.

Maps tables already created by `alembic/versions/20260816_0001_initial_schema.py`
(via `database/postgresql_schema_v0.1.sql`, unchanged since 2026-08-16) -- this
module does not create any new tables or columns, it only adds the ORM layer
that was missing for them. See the Paper Trading data-model PR summary for the
full comparison against `docs/concept/FXtrading_rebuild/03_ER図とデータ定義.md`
and `05_アーキテクチャと移行計画.md`. Known gap not resolved here:
`TradeOrder.status`'s DB CHECK constraint allows `cancel_pending`/`unknown` in
addition to the 7 values in the approved order state-transition table
(`05_アーキテクチャと移行計画.md`"注文状態遷移表", itself matching FR-ORD-05).

`Fill` and `LedgerEntry` intentionally have no status/lifecycle column: both
are append-only records of things that already happened (see ER doc §1
"監査ログと台帳は追記専用にする" and §4 "ledger_entry、fill、audit_logは物理
削除しない"). Do not add one without re-checking against the live schema.

Foreign keys to `exchange_connection`, `external_account`, and `instrument`
(for `TradingAccount`/`TradeOrder`/`TradingBot`(N/A here)/`TradingPosition`)
are plain schema-qualified references with no `relationship()`, matching the
existing convention in `app/models/market_data.py`. This is deliberate: those
target tables are outside Paper Trading's scope, and adding a `relationship()`
would require touching their own modules (`connections.py`, `instruments.py`)
for something unrelated to this change.

`relationship()` (2026-09-20 addition): this module, `app/models/strategy.py`,
and the `AccountSelectionPolicy`/`SystemEvent` support classes now declare
bidirectional `relationship()`s between each other -- see
`app/models/strategy.py`'s module docstring for the full rationale (why this
diverges from `market_data.py`'s plain-FK style, why no `cascade` argument is
used anywhere, and why cross-module type hints here are `TYPE_CHECKING`-only
in both directions rather than a real import on one side).
`OrderIntent.signal_id`/`risk_decision_id` are each unique in the DB (see
`uq_order_intent_signal`/`uq_order_intent_risk` below), so the reverse
relationships on `Signal`/`RiskDecision` use `uselist=False`; nothing else in
this module has a uniquely-constrained FK, so every other reverse
relationship is an ordinary list.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.connections import AccountSelectionPolicy
    from app.models.strategy import RiskDecision, Signal, TradingBot

SCHEMA = settings.database_schema


class TradingAccount(Base):
    __tablename__ = "trading_account"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.exchange_connection.id", ondelete="RESTRICT")
    )
    external_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_account.id", ondelete="RESTRICT")
    )
    mode: Mapped[str] = mapped_column(Text)
    base_currency: Mapped[str] = mapped_column(String(16))
    selection_mode: Mapped[str] = mapped_column(Text, server_default="manual")
    selection_policy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.account_selection_policy.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    selection_policy: Mapped["AccountSelectionPolicy | None"] = relationship(
        back_populates="trading_accounts"
    )
    trading_bots: Mapped[list["TradingBot"]] = relationship(back_populates="account")
    trade_orders: Mapped[list["TradeOrder"]] = relationship(back_populates="account")
    trading_positions: Mapped[list["TradingPosition"]] = relationship(back_populates="account")
    ledger_transactions: Mapped[list["LedgerTransaction"]] = relationship(back_populates="account")
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="account")


class OrderIntent(Base):
    __tablename__ = "order_intent"
    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_order_intent_signal"),
        UniqueConstraint("risk_decision_id", name="uq_order_intent_risk"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    signal_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.signal.id", ondelete="RESTRICT"))
    risk_decision_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.risk_decision.id", ondelete="RESTRICT")
    )
    side: Mapped[str] = mapped_column(Text)
    order_type: Mapped[str] = mapped_column(Text)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    max_slippage_bps: Mapped[int | None]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    signal: Mapped["Signal"] = relationship(back_populates="order_intent")
    risk_decision: Mapped["RiskDecision"] = relationship(back_populates="order_intent")
    trade_orders: Mapped[list["TradeOrder"]] = relationship(back_populates="order_intent")


class TradeOrder(Base):
    """Maps the `trade_order` table (named to avoid the `order` reserved
    word; `03_ER図とデータ定義.md` calls the concept `order`)."""

    __tablename__ = "trade_order"
    __table_args__ = (
        UniqueConstraint("account_id", "client_order_id", name="uq_trade_order_client_id"),
        UniqueConstraint(
            "account_id",
            "external_order_id",
            name="uq_trade_order_external_id",
            postgresql_nulls_not_distinct=True,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trading_account.id", ondelete="RESTRICT")
    )
    order_intent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.order_intent.id", ondelete="RESTRICT")
    )
    client_order_id: Mapped[str] = mapped_column(Text)
    external_order_id: Mapped[str | None] = mapped_column(Text)
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    side: Mapped[str] = mapped_column(Text)
    order_type: Mapped[str] = mapped_column(Text)
    time_in_force: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), server_default="0")
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["TradingAccount"] = relationship(back_populates="trade_orders")
    order_intent: Mapped["OrderIntent | None"] = relationship(back_populates="trade_orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fill"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "external_fill_id",
            name="uq_fill_external_id",
            postgresql_nulls_not_distinct=True,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trade_order.id", ondelete="RESTRICT")
    )
    external_fill_id: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), server_default="0")
    fee_asset: Mapped[str | None] = mapped_column(String(32))
    liquidity_role: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    order: Mapped["TradeOrder"] = relationship(back_populates="fills")
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="fill")


class TradingPosition(Base):
    """Maps the `trading_position` table (named to avoid the `position`
    reserved word; `03_ER図とデータ定義.md` calls the concept `position`)."""

    __tablename__ = "trading_position"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trading_account.id", ondelete="RESTRICT")
    )
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.instrument.id", ondelete="RESTRICT")
    )
    side: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    average_entry_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), server_default="0")
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), server_default="0")
    status: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["TradingAccount"] = relationship(back_populates="trading_positions")


class LedgerTransaction(Base):
    """Not named in `03_ER図とデータ定義.md`'s table list (which only
    describes `ledger_entry`'s own `transaction_id` column) but already
    exists as its own table in the live DB; included so `LedgerEntry`'s
    required foreign key resolves to a mapped class."""

    __tablename__ = "ledger_transaction"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trading_account.id", ondelete="RESTRICT")
    )
    reference_type: Mapped[str] = mapped_column(Text)
    reference_id: Mapped[UUID | None]
    description: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["TradingAccount"] = relationship(back_populates="ledger_transactions")
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="transaction")


class LedgerEntry(Base):
    """Append-only; see module docstring. No status column by design."""

    __tablename__ = "ledger_entry"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.ledger_transaction.id", ondelete="RESTRICT")
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trading_account.id", ondelete="RESTRICT")
    )
    fill_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.fill.id", ondelete="RESTRICT")
    )
    asset: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    entry_type: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    transaction: Mapped["LedgerTransaction"] = relationship(back_populates="ledger_entries")
    account: Mapped["TradingAccount"] = relationship(back_populates="ledger_entries")
    fill: Mapped["Fill | None"] = relationship(back_populates="ledger_entries")
