from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base
from app.models.workspace import Workspace

if TYPE_CHECKING:
    from app.models.trading import TradingAccount

SCHEMA = settings.database_schema


class Exchange(Base):
    __tablename__ = "exchange"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connections: Mapped[list["ExchangeConnection"]] = relationship(back_populates="exchange")


class Market(Base):
    __tablename__ = "market"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(Text, unique=True)
    asset_class: Mapped[str] = mapped_column(Text)
    product_type: Mapped[str] = mapped_column(Text)
    settlement_type: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExchangeConnection(Base):
    __tablename__ = "exchange_connection"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    exchange_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.exchange.id", ondelete="RESTRICT")
    )
    label: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    api_base_url: Mapped[str] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending_credentials")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credentials_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_outcome: Mapped[str] = mapped_column(Text, server_default="not_verified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped[Workspace] = relationship(back_populates="connections")
    exchange: Mapped[Exchange] = relationship(back_populates="connections")
    external_accounts: Mapped[list["ExternalAccount"]] = relationship(back_populates="connection")

    @property
    def credentials_status(self) -> str:
        return "saved" if self.secret_ref else "missing"


class ExternalAccount(Base):
    __tablename__ = "external_account"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.exchange_connection.id", ondelete="CASCADE")
    )
    external_account_ref_encrypted: Mapped[str] = mapped_column(Text)
    external_account_ref_hash: Mapped[str] = mapped_column(Text)
    external_account_ref_masked: Mapped[str] = mapped_column(Text)
    alias: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    hedging_enabled: Mapped[bool | None] = mapped_column(Boolean)
    margin_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    mt4_account_ref_masked: Mapped[str | None] = mapped_column(Text)
    gslo_mode: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connection: Mapped[ExchangeConnection] = relationship(back_populates="external_accounts")


class AccountSelectionPolicy(Base):
    """Added for Paper Trading's `TradingAccount.selection_policy_id` foreign
    key to resolve (see `app/models/strategy.py` module docstring for why
    this is needed at all). Already existed in the live DB; not a new table."""

    __tablename__ = "account_selection_policy"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "name", "version", name="uq_account_selection_policy_version"
        ),
        UniqueConstraint("workspace_id", "checksum", name="uq_account_selection_policy_checksum"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[int]
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trading_accounts: Mapped[list["TradingAccount"]] = relationship(
        back_populates="selection_policy"
    )


class WorkspaceAccountSelection(Base):
    __tablename__ = "workspace_account_selection"
    __table_args__ = (
        UniqueConstraint("workspace_id", "exchange_id", name="uq_workspace_exchange_selection"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    exchange_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.exchange.id", ondelete="RESTRICT")
    )
    external_account_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.external_account.id", ondelete="CASCADE")
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
