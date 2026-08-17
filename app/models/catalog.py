from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base

SCHEMA = settings.database_schema


class Workspace(Base):
    __tablename__ = "workspace"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connections: Mapped[list["ExchangeConnection"]] = relationship(back_populates="workspace")


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped[Workspace] = relationship(back_populates="connections")
    exchange: Mapped[Exchange] = relationship(back_populates="connections")
    external_accounts: Mapped[list["ExternalAccount"]] = relationship(back_populates="connection")


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
