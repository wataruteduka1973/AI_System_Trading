"""The workspace's event log (docs/plans/event-log.md): search and page through the
`system_event` rows, and the values to filter by. Read-only; every member of the workspace may
read it, like the halts and the system status."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.workspace import AppUser
from app.monitoring import event_log
from app.schemas.events import EventFacetsRead, EventPageRead, SystemEventRead
from app.security.rbac import require_viewer_role

router = APIRouter(prefix="/workspaces/{workspace_id}/events", tags=["events"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]


@router.get("", response_model=EventPageRead)
def list_events(
    workspace_id: UUID,
    db: DatabaseSession,
    _viewer: Viewer,
    severity: Annotated[list[str] | None, Query()] = None,
    category: str | None = None,
    event_type: str | None = None,
    reason_code: str | None = None,
    bot_id: UUID | None = None,
    correlation_id: UUID | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    q: Annotated[str | None, Query(max_length=event_log.MAX_TEXT_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=event_log.MAX_PAGE_SIZE)] = 50,
    before: datetime | None = None,
    before_id: UUID | None = None,
) -> EventPageRead:
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
    page = event_log.query_events(
        db,
        workspace_id,
        event_log.EventFilters(
            severities=severity or [],
            category=category,
            event_type=event_type,
            reason_code=reason_code,
            bot_id=bot_id,
            correlation_id=correlation_id,
            from_time=from_time,
            to_time=to_time,
            text=q,
        ),
        limit=limit,
        before=(before, before_id) if before is not None and before_id is not None else None,
    )
    return EventPageRead(
        items=[SystemEventRead.model_validate(event) for event in page.items],
        next_before=page.next_before[0] if page.next_before else None,
        next_before_id=page.next_before[1] if page.next_before else None,
    )


@router.get("/facets", response_model=EventFacetsRead)
def get_event_facets(workspace_id: UUID, db: DatabaseSession, _viewer: Viewer) -> EventFacetsRead:
    facets = event_log.event_facets(db, workspace_id)
    return EventFacetsRead(
        severities=facets.severities,
        categories=facets.categories,
        event_types=facets.event_types,
        reason_codes=facets.reason_codes,
    )
