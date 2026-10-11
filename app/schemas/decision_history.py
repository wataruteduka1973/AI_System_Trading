from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class FailedCheckRead(BaseModel):
    name: str
    """The Risk Gate check that did not pass (`binance_no_short`, `per_trade_loss` ...)."""
    detail: dict[str, object]
    """The check's own figures, without the `passed` flag."""


class DecisionItemRead(BaseModel):
    """One bar the bot evaluated (docs/plans/decision-history.md)."""

    signal_id: UUID
    evaluated_at: datetime
    bar_open_time: datetime | None
    bar_close_time: datetime | None
    action: str
    """The strategy's signal: `buy`, `sell` or `hold`."""
    outcome: str
    """`hold`, `ordered`, `closed`, `denied`, `halted` or `paused`."""
    rationale: dict[str, object]
    risk_outcome: str | None
    """`allow`, `allow_with_adjustment` or `deny`; `None` when the signal never reached the gate."""
    risk_reason_code: str | None
    failed_checks: list[FailedCheckRead]
    approved_quantity: Decimal | None
    order_id: UUID | None
    order_side: str | None
    fill_price: Decimal | None
    fill_quantity: Decimal | None


class DecisionSummaryRead(BaseModel):
    evaluations: int
    counts: dict[str, int]
    """Per outcome, over every evaluation in the time range (not only this page)."""


class DecisionHaltRead(BaseModel):
    id: UUID
    scope_type: str
    level: str
    reason_code: str
    status: str
    halted_at: datetime
    released_at: datetime | None


class DecisionHistoryRead(BaseModel):
    items: list[DecisionItemRead]
    summary: DecisionSummaryRead
    halts: list[DecisionHaltRead]
    """The halts on the bot, its account or the workspace that overlap the time this page covers."""
    next_before: datetime | None
    next_before_id: UUID | None
