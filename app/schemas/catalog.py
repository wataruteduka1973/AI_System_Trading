from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr


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


class ExchangeConnectionCreate(BaseModel):
    exchange_code: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=120)
    environment: Literal["practice", "testnet", "paper_data"]
    api_base_url: AnyHttpUrl
    credentials: dict[str, SecretStr] = Field(min_length=1)

    def revealed_credentials(self) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in self.credentials.items()}


class ExchangeCredentialsUpdate(BaseModel):
    credentials: dict[str, SecretStr] = Field(min_length=1)

    def revealed_credentials(self) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in self.credentials.items()}


class OandaAccountRead(BaseModel):
    account_ref_masked: str
    alias: str | None
    currency: str
    hedging_enabled: bool | None
    margin_rate: str | None
    gslo_mode: str | None
    usd_jpy_tradeable: bool


class OandaVerificationRead(BaseModel):
    connection_id: UUID
    status: str
    accounts: list[OandaAccountRead]


class BinanceAccountRead(BaseModel):
    account_ref_masked: str
    account_type: str
    permissions: list[str]
    can_trade: bool
    can_deposit: bool
    can_withdraw: bool
    nonzero_asset_count: int
    btc_jpy_tradeable: bool


class BinanceVerificationRead(BaseModel):
    connection_id: UUID
    status: str
    accounts: list[BinanceAccountRead]
