from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import OrmModel

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
    next_run_at: datetime
    consecutive_failures: int


class MarketDataSubscriptionUpdate(BaseModel):
    instrument_id: UUID
    timeframe: Timeframe = "1m"
    enabled: bool


class MarketDataCollectionUpdate(BaseModel):
    instrument_id: UUID
    enabled: bool


class MarketStreamTicketCreate(BaseModel):
    instrument_id: UUID
    timeframe: Timeframe = "1m"


class MarketStreamTicketRead(BaseModel):
    ticket: str
    exchange: str
    symbol: str
    timeframe: str
    expires_at: datetime


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
    next_run_at: datetime
    consecutive_failures: int
    blocked_reason: str | None


class MarketDataGapRead(BaseModel):
    """One stretch of missing candles (docs/plans/data-gaps.md)."""

    id: UUID
    instrument_id: UUID
    symbol: str
    exchange_code: str
    timeframe: str
    from_time: datetime
    to_time: datetime
    expected_count: int | None
    missing_count: int | None
    reason_code: str
    status: str
    """`open` needs attention; `ignored` is a hole the source itself has; `resolved` was filled."""
    detected_at: datetime
    resolved_at: datetime | None
    can_backfill: bool
    """Whether the screen may offer to fill it now."""
    latest_job: BackfillJobRead | None
    """The most recent backfill job made for this gap, with its progress and re-validation."""
    affected_bots: list[str]
    """Bots running or paused on the instrument."""


class MarketDataGapPageRead(BaseModel):
    total: int
    items: list[MarketDataGapRead]


class MarketDataGapSummaryRead(BaseModel):
    instrument_id: UUID
    symbol: str
    exchange_code: str
    timeframe: str
    open_count: int
    open_missing: int
    ignored_count: int
    resolved_count: int
