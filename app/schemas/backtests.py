"""Request/response schemas for the Backtest API
(`app/api/routes/backtests.py`, Horizon 4 "backtest + strategy governance"
phase). Paper-only in spirit like `app/schemas/trading.py`: there is only one
strategy implementation (`dummy_signal.py`), so nothing here lets the caller
pick a strategy -- `run_backtest_for_workspace` always uses the workspace's
shared dummy `StrategyVersion`/`RiskProfileVersion`.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.base import OrmModel


class BacktestCreate(BaseModel):
    instrument_id: UUID
    timeframe: str = "1m"
    from_time: datetime
    to_time: datetime
    initial_equity: Decimal = Field(gt=0)
    spread: Decimal = Field(default=Decimal(0), ge=0)
    mode: Literal["single", "walk_forward"] = "single"
    train_ratio: Decimal = Field(default=Decimal("0.7"), gt=0, lt=1)
    """Only used when `mode == "walk_forward"`."""

    @model_validator(mode="after")
    def _check_time_range(self) -> "BacktestCreate":
        if self.to_time <= self.from_time:
            raise ValueError("to_time must be after from_time")
        return self


class BacktestRunRead(OrmModel):
    id: UUID
    workspace_id: UUID
    strategy_version_id: UUID
    risk_profile_version_id: UUID
    dataset_snapshot_id: UUID
    parameters: dict[str, object]
    code_version: str
    status: str
    summary_metrics: dict[str, object]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class BacktestCreateResponse(BaseModel):
    runs: list[BacktestRunRead]
    """One run for `mode="single"`; `[train, test]` for `mode="walk_forward"`
    (see each run's `parameters.walk_forward_role`)."""


class BacktestTradeRead(OrmModel):
    id: UUID
    backtest_run_id: UUID
    sequence_no: int
    instrument_id: UUID
    side: str
    entry_time: datetime
    exit_time: datetime | None
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    fees: Decimal
    realized_pnl: Decimal | None
