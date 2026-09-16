from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.session import Base

SCHEMA = settings.database_schema


class Instrument(Base):
    __tablename__ = "instrument"
    __table_args__ = (
        UniqueConstraint("exchange_id", "market_id", "symbol", name="uq_instrument_symbol"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    exchange_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.exchange.id", ondelete="RESTRICT")
    )
    market_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.market.id", ondelete="RESTRICT"))
    symbol: Mapped[str] = mapped_column(Text)
    base_asset: Mapped[str] = mapped_column(Text)
    quote_asset: Mapped[str] = mapped_column(Text)
    contract_size: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    price_scale: Mapped[int] = mapped_column(SmallInteger)
    quantity_scale: Mapped[int] = mapped_column(SmallInteger)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    step_size: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    min_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    max_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    min_notional: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    margin_asset: Mapped[str | None] = mapped_column(Text)
    allowed_order_types: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    rules_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
