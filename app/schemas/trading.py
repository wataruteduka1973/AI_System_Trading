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
from typing import Literal
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
    # Only the approved strategy's validated timeframe (paper_provisioning.APPROVED_TIMEFRAME).
    timeframe: Literal["4h"] = "4h"


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


class EmergencyStopRequest(BaseModel):
    close_positions: bool = False
    """The emergency stop's 決済 policy: leave open positions as they are (default) or
    close them at market after the stop is in place."""
    reason: str | None = Field(default=None, max_length=500)


class EmergencyStopRead(BaseModel):
    halt_id: UUID
    scope_type: str
    scope_id: UUID | None
    level: str
    already_active: bool
    stopped_bot_ids: list[UUID]
    bot_stop_failures: list[dict[str, str]]
    closing_order_ids: list[UUID]
    close_failures: list[dict[str, str]]


class PositionOverviewRead(BaseModel):
    side: str
    quantity: Decimal
    average_entry_price: Decimal | None
    mark_price: Decimal | None
    """The latest final candle's close; `None` when there is no candle."""
    unrealized_pnl: Decimal
    stop_price: Decimal | None
    opened_at: datetime | None


class BotOverviewRead(BaseModel):
    """One bot with its account's standing (docs/plans/bot-overview.md). Amounts are in
    `quote_asset`."""

    bot_id: UUID
    name: str
    symbol: str
    exchange_code: str
    timeframe: str
    desired_state: str
    actual_state: str
    account_id: UUID
    quote_asset: str
    deposits: Decimal
    cash: Decimal
    equity: Decimal
    return_pct: Decimal | None
    """`(equity - deposits) / deposits` in percent; `None` when nothing was deposited."""
    realized_pnl: Decimal
    fees_paid: Decimal
    closed_trades: int
    position: PositionOverviewRead | None


class AccountOverviewRead(BaseModel):
    id: UUID
    base_currency: str
    mode: str
    status: str
    bot_names: list[str]
    """The bots trading on this account; empty for an account nothing uses."""
    balances: dict[str, Decimal]
    """Cash balance per asset."""


class BotOverviewResponse(BaseModel):
    bots: list[BotOverviewRead]
    accounts: list[AccountOverviewRead]


class FillRowRead(BaseModel):
    """One execution with the order behind it (docs/plans/trade-history.md). Amounts are in the
    quote asset of the instrument."""

    fill_id: UUID
    order_id: UUID
    executed_at: datetime
    symbol: str
    quote_asset: str
    side: str
    order_type: str
    order_status: str
    client_order_id: str
    quantity: Decimal
    price: Decimal
    notional: Decimal
    fee_amount: Decimal
    realized_pnl: Decimal
    """What closing (part of) a position made on this fill, before fees; 0 for an opening one."""
    reason: str
    """`signal`, `stop_loss`, `reversal_close`, `emergency_close` or `manual`."""
    bot_id: UUID | None
    bot_name: str | None


class FillTotalsRead(BaseModel):
    quote_asset: str
    fills: int
    fees: Decimal
    realized_pnl: Decimal


class FillPageRead(BaseModel):
    items: list[FillRowRead]
    totals: list[FillTotalsRead]
    """Over everything the filters match (not only this page), per quote asset."""
    next_before: datetime | None
    next_before_id: UUID | None


class PositionHistoryRead(BaseModel):
    id: UUID
    symbol: str
    quote_asset: str
    bot_id: UUID | None
    bot_name: str | None
    side: str
    status: str
    quantity: Decimal
    """0 once closed; the fills hold the sizes."""
    average_entry_price: Decimal | None
    realized_pnl: Decimal
    stop_price: Decimal | None
    opened_at: datetime | None
    closed_at: datetime | None


class PositionPageRead(BaseModel):
    total: int
    items: list[PositionHistoryRead]
