"""The workspace's trade history (FR-UI-12, docs/plans/trade-history.md): fills and positions.
Read-only; every member of the workspace may read it."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.workspace import AppUser
from app.schemas.trading import (
    FillPageRead,
    FillRowRead,
    FillTotalsRead,
    PositionHistoryRead,
    PositionPageRead,
)
from app.security.rbac import require_viewer_role
from app.trading.application import trade_history

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["trade-history"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]


@router.get("/fills", response_model=FillPageRead)
def list_fills(
    workspace_id: UUID,
    db: DatabaseSession,
    _viewer: Viewer,
    account_id: UUID | None = None,
    bot_id: UUID | None = None,
    instrument_id: UUID | None = None,
    side: Literal["buy", "sell"] | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=trade_history.MAX_PAGE_SIZE)] = 50,
    before: datetime | None = None,
    before_id: UUID | None = None,
) -> FillPageRead:
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
    page = trade_history.list_fills(
        db,
        workspace_id,
        trade_history.FillFilters(
            account_id=account_id,
            bot_id=bot_id,
            instrument_id=instrument_id,
            side=side,
            from_time=from_time,
            to_time=to_time,
        ),
        limit=limit,
        before=(before, before_id) if before is not None and before_id is not None else None,
    )
    return FillPageRead(
        items=[
            FillRowRead(
                fill_id=row.fill.id,
                order_id=row.order.id,
                executed_at=row.fill.executed_at,
                symbol=row.symbol,
                quote_asset=row.quote_asset,
                side=row.order.side,
                order_type=row.order.order_type,
                order_status=row.order.status,
                client_order_id=row.order.client_order_id,
                quantity=row.fill.quantity,
                price=row.fill.price,
                notional=row.fill.price * row.fill.quantity,
                fee_amount=row.fill.fee_amount,
                realized_pnl=row.realized_pnl,
                reason=row.reason,
                bot_id=row.bot_id,
                bot_name=row.bot_name,
            )
            for row in page.items
        ],
        totals=[
            FillTotalsRead(
                quote_asset=total.quote_asset,
                fills=total.fills,
                fees=total.fees,
                realized_pnl=total.realized_pnl,
            )
            for total in page.totals
        ],
        next_before=page.next_before[0] if page.next_before else None,
        next_before_id=page.next_before[1] if page.next_before else None,
    )


@router.get("/position-history", response_model=PositionPageRead)
def list_position_history(
    workspace_id: UUID,
    db: DatabaseSession,
    _viewer: Viewer,
    position_status: Annotated[
        list[Literal["open", "closed"]] | None, Query(alias="status")
    ] = None,
    bot_id: UUID | None = None,
    instrument_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=trade_history.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PositionPageRead:
    total, rows = trade_history.list_positions(
        db,
        workspace_id,
        statuses=list(position_status or []),
        bot_id=bot_id,
        instrument_id=instrument_id,
        limit=limit,
        offset=offset,
    )
    return PositionPageRead(
        total=total,
        items=[
            PositionHistoryRead(
                id=row.position.id,
                symbol=row.symbol,
                quote_asset=row.quote_asset,
                bot_id=row.bot_id,
                bot_name=row.bot_name,
                side=row.position.side,
                status=row.position.status,
                quantity=row.position.quantity,
                average_entry_price=row.position.average_entry_price,
                realized_pnl=row.position.realized_pnl,
                stop_price=row.position.stop_price,
                opened_at=row.position.opened_at,
                closed_at=row.position.closed_at,
            )
            for row in rows
        ],
    )
