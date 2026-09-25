"""Request/response schemas for the Bot management API
(`app/api/routes/trading.py`, Horizon5 "trade bot core functionality" phase).

Paper-only for now, matching every trading application module in this
codebase (`bot_lifecycle.validate_bot_startup` hard-fails any
`execution_mode != 'paper'` -- live is not implemented anywhere): request
schemas below have no `mode`/`execution_mode` field to set, since the route
handlers always create `mode='paper'` accounts and `execution_mode='paper'`
bots.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import OrmModel


class TradingAccountCreate(BaseModel):
    connection_id: UUID
    base_currency: str = Field(min_length=1, max_length=16)


class TradingAccountRead(OrmModel):
    id: UUID
    workspace_id: UUID
    connection_id: UUID | None
    mode: str
    base_currency: str
    status: str
    created_at: datetime


class TradingAccountDepositCreate(BaseModel):
    """`amount`/`asset` are required, not defaulted -- matches
    `account_funding.seed_paper_deposit`'s own documented principle that
    there is no product decision yet on what a paper account should start
    with, so this endpoint does not invent one either."""

    amount: Decimal = Field(gt=0)
    asset: str = Field(min_length=1, max_length=32)
    note: str | None = None


class TradingAccountDepositRead(OrmModel):
    id: UUID
    account_id: UUID
    reference_type: str
    description: str
    occurred_at: datetime


class TradingBotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_id: UUID
    instrument_id: UUID
    timeframe: str = "1m"


class TradingBotRead(OrmModel):
    id: UUID
    workspace_id: UUID
    name: str
    execution_mode: str
    strategy_mode: str
    account_id: UUID
    instrument_id: UUID
    timeframe: str
    desired_state: str
    actual_state: str
    live_trading_enabled: bool
    version: int
    created_at: datetime


class BotRunRead(OrmModel):
    id: UUID
    bot_id: UUID
    status: str
    code_version: str
    started_at: datetime
    stopped_at: datetime | None
    stop_reason: str | None
    heartbeat_at: datetime | None


class LatestSignalRead(OrmModel):
    id: UUID
    action: str
    created_at: datetime


class BotRunSummaryRead(OrmModel):
    """Response for `GET .../bots/{id}/latest-run` (minimal UI task,
    2026-09-25): the most recent `BotRun` regardless of status, plus the most
    recent `Signal` recorded against it, if any. Distinct from `BotRunRead`
    (used only by the start/pause/resume/stop action responses) -- this is
    always hand-assembled in the route (two queries joined in Python, not one
    ORM relationship), so keeping it a separate schema avoids coupling that
    assembly to the simpler lifecycle-action response shape."""

    id: UUID
    bot_id: UUID
    status: str
    code_version: str
    started_at: datetime
    stopped_at: datetime | None
    stop_reason: str | None
    heartbeat_at: datetime | None
    latest_signal: LatestSignalRead | None
