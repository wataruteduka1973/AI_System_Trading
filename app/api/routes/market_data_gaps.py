"""Missing candles (FR-UI-10, docs/plans/data-gaps.md): the gaps a workspace can see, a per-series
summary, and filling one gap by queueing a backfill for its stretch. Reading is open to every
member; filling needs the Operator role, like any other backfill."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.routes.market_data import application_errors, validate_collection_access
from app.db.session import get_db
from app.market_data.application import gap_reports
from app.market_data.application import use_cases as market_data_application
from app.models.market_data import BackfillJob
from app.models.workspace import AppUser
from app.schemas.market_data import (
    BackfillJobRead,
    MarketDataGapPageRead,
    MarketDataGapRead,
    MarketDataGapSummaryRead,
    Timeframe,
)
from app.security.rbac import require_operator_role, require_viewer_role

router = APIRouter(prefix="/workspaces/{workspace_id}/market-data-gaps", tags=["market-data"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]
Operator = Annotated[AppUser, Depends(require_operator_role)]
GapStatus = Annotated[
    list[str] | None, Query(description="open / ignored / resolved; several allowed")
]


def _read(row: gap_reports.GapRow) -> MarketDataGapRead:
    gap = row.gap
    return MarketDataGapRead(
        id=gap.id,
        instrument_id=gap.instrument_id,
        symbol=row.symbol,
        exchange_code=row.exchange_code,
        timeframe=gap.timeframe,
        from_time=gap.from_time,
        to_time=gap.to_time,
        expected_count=gap.expected_count,
        missing_count=gap.missing_count,
        reason_code=gap.reason_code,
        status=gap.status,
        detected_at=gap.detected_at,
        resolved_at=gap.resolved_at,
        can_backfill=row.can_backfill,
        latest_job=BackfillJobRead.model_validate(row.latest_job) if row.latest_job else None,
        affected_bots=row.affected_bots,
    )


@router.get("", response_model=MarketDataGapPageRead)
def list_market_data_gaps(
    workspace_id: UUID,
    db: DatabaseSession,
    _viewer: Viewer,
    gap_status: Annotated[GapStatus, Query(alias="status")] = None,
    instrument_id: UUID | None = None,
    timeframe: Timeframe | None = None,
    limit: Annotated[int, Query(ge=1, le=gap_reports.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MarketDataGapPageRead:
    with application_errors():
        market_data_application.require_workspace(db, workspace_id)
    total, rows = gap_reports.list_gaps(
        db,
        workspace_id,
        statuses=gap_status or [],
        instrument_id=instrument_id,
        timeframe=timeframe,
        limit=limit,
        offset=offset,
    )
    return MarketDataGapPageRead(total=total, items=[_read(row) for row in rows])


@router.get("/summary", response_model=list[MarketDataGapSummaryRead])
def get_market_data_gap_summary(
    workspace_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> list[MarketDataGapSummaryRead]:
    with application_errors():
        market_data_application.require_workspace(db, workspace_id)
    return [
        MarketDataGapSummaryRead(
            instrument_id=row.instrument_id,
            symbol=row.symbol,
            exchange_code=row.exchange_code,
            timeframe=row.timeframe,
            open_count=row.open_count,
            open_missing=row.open_missing,
            ignored_count=row.ignored_count,
            resolved_count=row.resolved_count,
        )
        for row in gap_reports.summarize_gaps(db, workspace_id)
    ]


@router.post(
    "/{gap_id}/backfill",
    response_model=BackfillJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def backfill_market_data_gap(
    workspace_id: UUID, gap_id: UUID, db: DatabaseSession, _operator: Operator
) -> BackfillJob:
    with application_errors():
        return market_data_application.enqueue_gap_backfill(
            db, workspace_id, gap_id, validate_collection_access
        )
