"""Authentication and workspace-role authorization dependencies (Horizon5
Group A -- docs/plans/horizon5-implementation-plan.md Units 3/4). Replaces
`app.security.auth.require_owner`'s single fixed dev token with real
per-user, per-workspace access control backed by `app.models.workspace
.UserMembership`.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.workspace import AppUser, UserMembership
from app.security.session import SessionTokenError, is_session_revoked, verify_session_token

_ROLE_SEVERITY = {"viewer": 1, "operator": 2, "owner": 3}


def require_authenticated_user(
    db: Annotated[Session, Depends(get_db)],
    session: Annotated[str | None, Cookie()] = None,
) -> AppUser:
    if session is None or settings.session_signing_secret is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        claims = verify_session_token(
            session, secret=settings.session_signing_secret.get_secret_value()
        )
    except SessionTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        ) from exc
    user = db.get(AppUser, claims.app_user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if is_session_revoked(db, user_id=user.id, issued_at=claims.issued_at):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    return user


def require_workspace_role(minimum_role: str) -> Callable[..., AppUser]:
    """Returns a dependency that requires the current user to hold at least
    `minimum_role` in the `workspace_id` path parameter's workspace. A
    non-member gets 404 (not 403): the workspace's existence itself is not
    disclosed to someone who is not a member of it. Someone who *is* a
    member, just with too weak a role, gets 403 -- they can already see the
    workspace exists."""

    def _make(
        workspace_id: UUID,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[AppUser, Depends(require_authenticated_user)],
    ) -> AppUser:
        membership = db.scalar(
            select(UserMembership).where(
                UserMembership.workspace_id == workspace_id,
                UserMembership.user_id == current_user.id,
            )
        )
        if membership is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        if _ROLE_SEVERITY[membership.role] < _ROLE_SEVERITY[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role"
            )
        return current_user

    return _make


require_viewer_role: Callable[..., AppUser] = require_workspace_role("viewer")
require_operator_role: Callable[..., AppUser] = require_workspace_role("operator")
require_owner_role: Callable[..., AppUser] = require_workspace_role("owner")
"""Module-level singletons (2026-09-21, discovered necessary in this Unit's own
self-review): every route module needs the *same* callable object per role, not
a fresh one from calling `require_workspace_role(...)` itself at each import
site. FastAPI's `app.dependency_overrides` (and, more importantly here,
`tests/*_api.py`'s override-based tests) key on object identity -- two calls to
`require_workspace_role("viewer")` from two different route modules produce two
distinct closures, so overriding one in a test would silently leave the other
route's real DB-backed check running. Route modules import these three names
instead of calling the factory themselves."""


def require_any_workspace_owner(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AppUser, Depends(require_authenticated_user)],
) -> AppUser:
    """For workspace-independent, global operations (trading_halt management
    is workspace-scoped and uses require_workspace_role instead; this is for
    actions like forcing another user's sessions to end, which have no
    workspace_id of their own). Requires 'owner' in at least one workspace,
    not any particular one."""
    has_ownership = db.scalar(
        select(UserMembership.workspace_id)
        .where(UserMembership.user_id == current_user.id, UserMembership.role == "owner")
        .limit(1)
    )
    if has_ownership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workspace owner role required"
        )
    return current_user
