"""Session JWTs (PyJWT-signed, HS256) for authenticated users.

Mirrors `app/market_data/infrastructure/stream_tickets.py`'s design (same
signing library, same `iat`/`exp` NumericDate truncation-to-seconds
reasoning) rather than introducing a second JWT convention. Unlike stream
tickets, a session is not one-time-use: no `UsedTicketStore`-equivalent here.

**Revocation (Horizon5 Group A critical-review fix #1,
docs/plans/horizon5-implementation-plan.md Unit 3.9)**: a session JWT cannot
be revoked by invalidating the token itself (it is a self-contained signed
credential, not a server-side handle) -- revocation instead works by
recording, per user, a cutoff timestamp (`app.models.workspace.SessionRevocation
.revoked_sessions_before`) and rejecting any session whose `iat` predates it.
`revoke_sessions` below writes that cutoff; `app.security.rbac.
require_authenticated_user` is what actually enforces it on each request.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workspace import SessionRevocation

_ALGORITHM = "HS256"
_COOKIE_NAME = "session"


class SessionTokenError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SessionClaims:
    app_user_id: UUID
    issued_at: datetime


def issue_session_token(
    *, app_user_id: UUID, secret: str, ttl_seconds: int
) -> tuple[str, datetime]:
    """Sign a new session JWT. Returns (token, expires_at). `iat`/`exp` are
    truncated to whole seconds before signing (see stream_tickets.py's
    issue_ticket docstring for why: PyJWT's NumericDate encoding is
    second-precision, so truncating here keeps the value this function
    returns consistent with what decoding later reconstructs)."""
    now = datetime.now(UTC).replace(microsecond=0)
    expires_at = now + timedelta(seconds=ttl_seconds)
    payload = {"sub": str(app_user_id), "iat": now, "exp": expires_at}
    token = jwt.encode(payload, secret, algorithm=_ALGORITHM)
    return token, expires_at


def verify_session_token(token: str, *, secret: str) -> SessionClaims:
    """Validate signature and expiry only -- revocation is a separate check
    the caller (require_authenticated_user) makes against the database,
    since this function has no Session to query with."""
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise SessionTokenError("session_expired", "Session has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise SessionTokenError("session_invalid", "Session is invalid") from exc
    try:
        return SessionClaims(
            app_user_id=UUID(payload["sub"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise SessionTokenError("session_invalid", "Session is invalid") from exc


def revoke_sessions(db: Session, *, user_id: UUID, revoked_by: UUID) -> SessionRevocation:
    """Invalidate every session currently outstanding for `user_id` (upserts
    `revoked_sessions_before = now()`, truncated to whole seconds to match a
    JWT `iat`'s own precision -- otherwise a session issued in the same
    second as the revoke call could be spuriously rejected; see this
    function's own unit tests). `revoked_by == user_id` for self-service
    ("log me out everywhere"); a different value for an Owner forcing another
    user's sessions to end (`app.security.rbac.require_any_workspace_owner`)."""
    now = datetime.now(UTC).replace(microsecond=0)
    existing = db.get(SessionRevocation, user_id)
    if existing is None:
        existing = SessionRevocation(
            user_id=user_id, revoked_sessions_before=now, revoked_by=revoked_by
        )
        db.add(existing)
    else:
        existing.revoked_sessions_before = now
        existing.revoked_by = revoked_by
    db.flush()
    return existing


def is_session_revoked(db: Session, *, user_id: UUID, issued_at: datetime) -> bool:
    revocation = db.scalar(select(SessionRevocation).where(SessionRevocation.user_id == user_id))
    return revocation is not None and issued_at < revocation.revoked_sessions_before
