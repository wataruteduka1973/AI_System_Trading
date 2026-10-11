"""A bot's decision history (docs/plans/decision-history.md): each bar it evaluated -- the signal,
the risk decision, what came of it -- and the halts that stopped it entering. Read-only; every
member of the workspace may read it."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.strategy import TradingBot
from app.models.workspace import AppUser
from app.schemas.decision_history import (
    DecisionHaltRead,
    DecisionHistoryRead,
    DecisionItemRead,
    DecisionSummaryRead,
    FailedCheckRead,
)
from app.security.rbac import require_viewer_role
from app.trading.application import decision_history

router = APIRouter(prefix="/workspaces/{workspace_id}/bots/{bot_id}", tags=["bots"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]
Outcome = Literal["hold", "ordered", "closed", "denied", "halted", "paused"]


@router.get("/decisions", response_model=DecisionHistoryRead)
def list_bot_decisions(
    workspace_id: UUID,
    bot_id: UUID,
    db: DatabaseSession,
    _viewer: Viewer,
    outcome: Annotated[list[Outcome] | None, Query()] = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=decision_history.MAX_PAGE_SIZE)] = 50,
    before: datetime | None = None,
    before_id: UUID | None = None,
) -> DecisionHistoryRead:
    bot = db.get(TradingBot, bot_id)
    if bot is None or bot.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    if (before is None) != (before_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="before and before_id go together",
        )
    if from_time and to_time and to_time <= from_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="to_time must be after from_time",
        )
    page = decision_history.list_decisions(
        db,
        workspace_id,
        bot,
        outcomes=list(outcome or []),
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        before=(before, before_id) if before is not None and before_id is not None else None,
    )
    return DecisionHistoryRead(
        items=[
            DecisionItemRead(
                signal_id=entry.signal.id,
                evaluated_at=entry.signal.created_at,
                bar_open_time=entry.bar_open_time,
                bar_close_time=entry.bar_close_time,
                action=entry.signal.action,
                outcome=entry.outcome,
                rationale=entry.signal.rationale,
                risk_outcome=entry.risk_outcome,
                risk_reason_code=entry.risk_reason_code,
                failed_checks=[
                    FailedCheckRead(name=check.name, detail=check.detail)
                    for check in entry.failed_checks
                ],
                approved_quantity=entry.approved_quantity,
                order_id=entry.order_id,
                order_side=entry.order_side,
                fill_price=entry.fill_price,
                fill_quantity=entry.fill_quantity,
            )
            for entry in page.items
        ],
        summary=DecisionSummaryRead(
            evaluations=page.summary.evaluations, counts=page.summary.counts
        ),
        halts=[
            DecisionHaltRead(
                id=halt.id,
                scope_type=halt.scope_type,
                level=halt.level,
                reason_code=halt.reason_code,
                status=halt.status,
                halted_at=halt.halted_at,
                released_at=halt.released_at,
            )
            for halt in page.halts
        ],
        next_before=page.next_before[0] if page.next_before else None,
        next_before_id=page.next_before[1] if page.next_before else None,
    )
