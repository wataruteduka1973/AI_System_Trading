"""`GET /workspaces/{id}/system-status`: whether the workers and the pipeline around them are
healthy right now (docs/plans/system-status.md). Read-only, for any member of the workspace."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.workspace import AppUser
from app.monitoring.system_status import SystemStatus, build_system_status
from app.schemas.system_status import SystemStatusRead
from app.security.rbac import require_viewer_role

router = APIRouter(tags=["system-status"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]


@router.get("/workspaces/{workspace_id}/system-status", response_model=SystemStatusRead)
def get_system_status(workspace_id: UUID, db: DatabaseSession, _viewer: Viewer) -> SystemStatus:
    return build_system_status(
        db,
        workspace_id,
        now=datetime.now(UTC),
        trading_stale_after=timedelta(seconds=settings.trading_worker_stale_seconds),
        market_data_overdue_after=timedelta(seconds=settings.market_data_worker_overdue_seconds),
    )
