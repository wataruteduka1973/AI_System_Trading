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


Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


class CandleRead(BaseModel):
    open_time: datetime
    close_time: datetime
    open: str
    high: str
    low: str
    close: str
    volume: str | None
    trade_count: int | None
    source: str
    quality_status: str
    is_final: bool


class CandleCoverageRead(BaseModel):
    timeframe: Timeframe
    requested_from: datetime | None
    requested_to: datetime | None
    actual_from: datetime | None
    actual_to: datetime | None
    stored_count: int
    expected_count: int | None
    missing_count: int | None
    coverage_status: Literal[
        "complete", "partial_source_limit", "partial_gaps", "empty", "checking"
    ]
    source_limitation: str | None


class CandleBackfillCreate(BaseModel):
    instrument_id: UUID
    timeframe: Timeframe = "1m"
    days: int = Field(default=365, ge=1, le=365)


class BackfillJobRead(OrmModel):
    id: UUID
    workspace_id: UUID
    instrument_id: UUID
    timeframe: str
    from_time: datetime
    to_time: datetime
    trigger_type: str
    status: str
    attempts: int
    rows_written: int
    validation_result: dict[str, object]
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class MarketDataSubscriptionUpdate(BaseModel):
    instrument_id: UUID
    timeframe: Timeframe = "1m"
    enabled: bool


class MarketDataCollectionUpdate(BaseModel):
    instrument_id: UUID
    enabled: bool


class MarketDataSubscriptionRead(OrmModel):
    id: UUID
    workspace_id: UUID
    instrument_id: UUID
    timeframe: str
    enabled: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None


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
    credentials_status: Literal["saved", "missing"]
    credentials_updated_at: datetime | None
    verification_outcome: Literal[
        "not_verified", "success", "authentication_failed", "communication_failed"
    ]


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


class WorkspaceAccountRead(BaseModel):
    id: UUID
    connection_id: UUID
    exchange_id: UUID
    exchange_code: str
    connection_label: str
    connection_status: str
    account_ref_masked: str
    alias: str | None
    environment: str
    currency: str
    status: str
    selected: bool


class WorkspaceAccountSelectionUpdate(BaseModel):
    external_account_id: UUID


class WorkspaceAccountSelectionRead(BaseModel):
    workspace_id: UUID
    exchange_id: UUID
    external_account_id: UUID
    selected_at: datetime
