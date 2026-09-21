"""trading_halt management: list active halts and let an Owner release them
(Horizon5 Group A, Unit 4.4 -- docs/plans/horizon5-implementation-plan.md).

`docs/plans/trading-halt-mvp.md` (ADR 0004) implemented the `level`/`status`
state machine (`app/trading/application/trading_halt.py`) but never wired a
caller for its release functions -- this module is that caller. Both release
endpoints require `Owner`, matching `docs/decisions/0004-trading-halt-mvp
-scope.md`'s "解除はOwnerのみ".

`trading_halt.deescalate_one_step` takes a `HaltScope`
(workspace_id/scope_type/scope_id), not a `TradingHalt.id` -- the endpoints
below fetch the row by `id` first and derive the `HaltScope` from it, since
the URL path is id-based (a more natural REST shape than exposing
scope_type/scope_id directly) but the underlying function is scope-based.
`release_emergency_stop` takes the row directly, so no such conversion is
needed there.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.strategy import TradingHalt
from app.models.workspace import AppUser
from app.schemas.trading_halts import TradingHaltRead
from app.security.rbac import require_owner_role, require_viewer_role
from app.trading.application import trading_halt as trading_halt_app

router = APIRouter(prefix="/workspaces/{workspace_id}/trading-halts", tags=["trading-halts"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[AppUser, Depends(require_viewer_role)]
Owner = Annotated[AppUser, Depends(require_owner_role)]


def _get_halt(db: Session, workspace_id: UUID, halt_id: UUID) -> TradingHalt:
    """Only an *active* halt is returned -- an already-released one is treated
    as not-found (404), not re-releasable. This matters for both callers
    below: `release_trading_halt` relies on it to guarantee
    `deescalate_one_step` always finds an active row (see the assert there),
    and it stops `emergency_release_trading_halt` from silently re-stamping
    `released_at`/`released_by` on a halt someone already released."""
    halt = db.scalar(
        select(TradingHalt).where(
            TradingHalt.id == halt_id,
            TradingHalt.workspace_id == workspace_id,
            TradingHalt.status == "active",
        )
    )
    if halt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading halt not found")
    return halt


@router.get("", response_model=list[TradingHaltRead])
def list_active_trading_halts(
    workspace_id: UUID, db: DatabaseSession, _viewer: Viewer
) -> list[TradingHalt]:
    statement = (
        select(TradingHalt)
        .where(TradingHalt.workspace_id == workspace_id, TradingHalt.status == "active")
        .order_by(TradingHalt.halted_at.desc())
    )
    return list(db.scalars(statement).all())


@router.post("/{halt_id}/release", response_model=TradingHaltRead)
def release_trading_halt(
    workspace_id: UUID, halt_id: UUID, db: DatabaseSession, _owner: Owner
) -> TradingHalt:
    halt = _get_halt(db, workspace_id, halt_id)
    if halt.level == "emergency_stopped":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An emergency_stopped halt must be released via /emergency-release",
        )
    if halt.scope_id is None:
        # ADR 0004's two in-scope causes are always bot/account-scoped
        # (never system/workspace, the only scope_types with a NULL
        # scope_id); ck_trading_halt_scope enforces this at the DB level
        # too, so reaching here would mean a cause outside ADR 0004's scope
        # activated a halt some future task added -- not something this
        # endpoint knows how to handle yet.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This trading halt's scope is not supported by this endpoint",
        )
    scope = trading_halt_app.HaltScope(
        workspace_id=halt.workspace_id, scope_type=halt.scope_type, scope_id=halt.scope_id
    )
    updated = trading_halt_app.deescalate_one_step(
        db, scope, reason_code=halt.reason_code, now=datetime.now(UTC)
    )
    db.commit()
    assert updated is not None  # _get_halt already confirmed an active row exists
    db.refresh(updated)
    return updated


@router.post("/{halt_id}/emergency-release", response_model=TradingHaltRead)
def emergency_release_trading_halt(
    workspace_id: UUID,
    halt_id: UUID,
    db: DatabaseSession,
    owner: Owner,
) -> TradingHalt:
    halt = _get_halt(db, workspace_id, halt_id)
    try:
        updated = trading_halt_app.release_emergency_stop(
            db, halt, released_by=owner.id, now=datetime.now(UTC)
        )
    except trading_halt_app.TradingHaltError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(updated)
    return updated
