from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class WorkspaceInstrumentRead(BaseModel):
    id: UUID
    exchange_code: str
    market_code: str
    symbol: str
    base_asset: str
    quote_asset: str
    price_scale: int
    quantity_scale: int
    tick_size: str
    step_size: str
    min_quantity: str | None
    max_quantity: str | None
    min_notional: str | None
    allowed_order_types: list[str]
    capabilities: dict[str, object]
    status: str
    rules_synced_at: datetime | None


class WorkspaceInstrumentSyncRead(BaseModel):
    instruments: list[WorkspaceInstrumentRead]
