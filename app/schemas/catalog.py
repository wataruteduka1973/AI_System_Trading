from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceRead(OrmModel):
    id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


class ExchangeRead(OrmModel):
    id: UUID
    code: str
    name: str
    status: str


class MarketRead(OrmModel):
    id: UUID
    code: str
    asset_class: str
    product_type: str
    settlement_type: str


class ExchangeConnectionRead(OrmModel):
    id: UUID
    workspace_id: UUID
    exchange_id: UUID
    label: str
    environment: str
    api_base_url: str
    status: str
    capabilities: dict[str, object]
    last_verified_at: datetime | None
