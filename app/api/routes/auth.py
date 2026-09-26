"""OIDC login/callback/logout, current-user info, and session revocation
(Horizon5 Group A -- docs/plans/horizon5-implementation-plan.md Units 3/3.9/4.4).

Two cookies are involved, with different `SameSite` policies (see 2.1節 of the
plan): `oidc_state` is `SameSite=Lax` because it must survive the top-level
navigation back from the external IdP to `/auth/callback`, which `Strict`
would not send; `session` is `SameSite=Strict` since every other use of it is
a same-origin fetch from this app's own frontend, and `Strict` is the
stronger CSRF defense of the two.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.workspace import AppUser, UserMembership, Workspace
from app.schemas.auth import AuthenticatedUserRead, CurrentUserRead, WorkspaceMembershipRead
from app.security import oidc
from app.security.rbac import require_any_workspace_owner, require_authenticated_user
from app.security.session import issue_session_token, revoke_sessions

router = APIRouter(prefix="/auth", tags=["auth"])
DatabaseSession = Annotated[Session, Depends(get_db)]

_OIDC_STATE_COOKIE = "oidc_state"
_SESSION_COOKIE = "session"
_UPSTREAM_UNAVAILABLE_CODES = {"discovery_unavailable", "token_exchange_unavailable"}
"""`oidc.OidcError.code` values that mean "could not reach the IdP" rather than
"the IdP responded and rejected the request" -- these map to 502 (matching
connections.py's `communication_failed` -> 502 convention) instead of the
401/503 this module uses for the latter."""


def _raise_for_oidc_error(exc: oidc.OidcError) -> HTTPException:
    status_code = (
        status.HTTP_502_BAD_GATEWAY
        if exc.code in _UPSTREAM_UNAVAILABLE_CODES
        else status.HTTP_401_UNAUTHORIZED
    )
    return HTTPException(status_code=status_code, detail=str(exc))


@dataclass(frozen=True)
class _OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    state_secret: str
    session_secret: str


def _require_oidc_configured() -> _OidcConfig:
    if (
        settings.oidc_issuer is None
        or settings.oidc_client_id is None
        or settings.oidc_client_secret is None
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC login is not configured",
        )
    if settings.session_signing_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session signing is not configured",
        )
    session_secret = settings.session_signing_secret.get_secret_value()
    return _OidcConfig(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        state_secret=session_secret,
        session_secret=session_secret,
    )


@router.get("/login")
async def login() -> RedirectResponse:
    config = _require_oidc_configured()
    try:
        discovery = await oidc.fetch_discovery_document(config.issuer)
    except oidc.OidcError as exc:
        raise _raise_for_oidc_error(exc) from exc
    pkce = oidc.generate_pkce_pair()
    state = oidc.generate_state()
    nonce = oidc.generate_state()
    authorization_url = oidc.build_authorization_url(
        discovery,
        client_id=config.client_id,
        redirect_uri=settings.oidc_redirect_uri,
        scopes=settings.oidc_scopes,
        state=state,
        code_challenge=pkce.code_challenge,
        nonce=nonce,
    )
    pending_token = oidc.sign_pending_login(
        oidc.PendingLogin(state=state, code_verifier=pkce.code_verifier, nonce=nonce),
        secret=config.state_secret,
    )
    response = RedirectResponse(authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(
        _OIDC_STATE_COOKIE,
        pending_token,
        max_age=oidc.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.app_env != "local",
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(
    db: DatabaseSession,
    code: str,
    state: str,
    oidc_state: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    config = _require_oidc_configured()
    if oidc_state is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Login attempt expired")
    try:
        pending = oidc.verify_pending_login(oidc_state, secret=config.state_secret)
    except oidc.OidcError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if pending.state != state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="State mismatch")

    try:
        discovery = await oidc.fetch_discovery_document(config.issuer)
        tokens = await oidc.exchange_code_for_tokens(
            discovery.token_endpoint,
            code=code,
            code_verifier=pending.code_verifier,
            redirect_uri=settings.oidc_redirect_uri,
            client_id=config.client_id,
            client_secret=config.client_secret,
        )
        id_token = tokens["id_token"]
        claims = oidc.verify_id_token(
            id_token,
            jwks_uri=discovery.jwks_uri,
            audience=config.client_id,
            issuer=discovery.issuer,
            nonce=pending.nonce,
        )
    except oidc.OidcError as exc:
        raise _raise_for_oidc_error(exc) from exc

    try:
        oidc_subject = str(claims["sub"])
        email = str(claims["email"])
        display_name = str(claims.get("name", email))
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="ID token is missing a required claim"
        ) from exc

    user = _resolve_or_create_app_user(
        db, oidc_subject=oidc_subject, email=email, display_name=display_name
    )
    if user.status == "disabled":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    db.commit()

    token, _expires_at = issue_session_token(
        app_user_id=user.id,
        secret=config.session_secret,
        ttl_seconds=settings.session_ttl_seconds,
    )
    # A bare "/" only lands on the frontend when it and this API share an origin.
    # cors_origins[0] is already the frontend's own trusted origin (CORSMiddleware
    # would reject its requests otherwise) -- not request-supplied, so this is not
    # an open redirect -- and correctly sends the browser back to a separately
    # hosted frontend (e.g. Vite dev server on a different port) instead of 404ing
    # on this API's own root.
    response = RedirectResponse(
        settings.cors_origins[0] if settings.cors_origins else "/",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(_OIDC_STATE_COOKIE)
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.app_env != "local",
        samesite="strict",
    )
    return response


def _resolve_or_create_app_user(
    db: Session, *, oidc_subject: str, email: str, display_name: str
) -> AppUser:
    user = db.scalar(select(AppUser).where(AppUser.oidc_subject == oidc_subject))
    if user is not None:
        return user
    user = db.scalar(select(AppUser).where(AppUser.email == email))
    if user is not None:
        # /code-review finding (correctness): this branch used to unconditionally
        # set status="active", which silently reactivated a disabled account the
        # moment its email matched an OIDC login (the caller's own disabled check
        # runs *after* this function returns, so it always passed since status was
        # already overwritten). A disabled user is left untouched here -- the
        # caller's `user.status == "disabled"` check then correctly rejects them.
        if user.status == "disabled":
            return user
        # Invited via email (status="invited"); first successful login links
        # the OIDC subject and activates the account.
        user.oidc_subject = oidc_subject
        user.status = "active"
        return user
    user = AppUser(
        email=email, display_name=display_name, oidc_subject=oidc_subject, status="active"
    )
    db.add(user)
    db.flush()
    return user


@router.post("/logout")
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(_SESSION_COOKIE)
    return response


@router.get("/me", response_model=CurrentUserRead)
def read_current_user(
    db: DatabaseSession, current_user: Annotated[AppUser, Depends(require_authenticated_user)]
) -> CurrentUserRead:
    rows = db.execute(
        select(UserMembership, Workspace)
        .join(Workspace, UserMembership.workspace_id == Workspace.id)
        .where(UserMembership.user_id == current_user.id)
        .order_by(UserMembership.created_at)
    ).all()
    return CurrentUserRead(
        user=AuthenticatedUserRead.model_validate(current_user),
        memberships=[
            WorkspaceMembershipRead(
                workspace_id=membership.workspace_id,
                workspace_name=workspace.name,
                role=membership.role,
            )
            for membership, workspace in rows
        ],
    )


@router.post("/sessions/revoke")
def revoke_own_sessions(
    db: DatabaseSession, current_user: Annotated[AppUser, Depends(require_authenticated_user)]
) -> Response:
    """Self-service "log out everywhere": the user who thinks their own
    session may have leaked does not need to wait for an Owner (see
    /auth/users/{user_id}/revoke-sessions below) to act."""
    revoke_sessions(db, user_id=current_user.id, revoked_by=current_user.id)
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(_SESSION_COOKIE)
    return response


@router.post("/users/{user_id}/revoke-sessions")
def revoke_user_sessions(
    user_id: UUID,
    db: DatabaseSession,
    current_user: Annotated[AppUser, Depends(require_any_workspace_owner)],
) -> Response:
    """Owner-initiated forced revocation of another user's sessions (e.g. a
    suspected credential leak -- see trading_halt's "APIキー漏えい疑い" cause,
    which this endpoint exists to give a human response to on the auth
    side).

    `require_any_workspace_owner` only proves `current_user` owns *some*
    workspace -- with self-service workspace creation (any authenticated user
    can create one and becomes its Owner), that is trivially true for every
    user, not just real administrators. Without the shared-workspace check
    below this was an IDOR: anyone could create a throwaway workspace to
    become an Owner of *something*, then force-revoke an unrelated user's
    sessions (/code-review finding)."""
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    shares_an_owned_workspace = db.scalar(
        select(UserMembership.workspace_id)
        .where(UserMembership.user_id == user_id)
        .where(
            UserMembership.workspace_id.in_(
                select(UserMembership.workspace_id).where(
                    UserMembership.user_id == current_user.id,
                    UserMembership.role == "owner",
                )
            )
        )
        .limit(1)
    )
    if shares_an_owned_workspace is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target user is not a member of a workspace you own",
        )
    revoke_sessions(db, user_id=user_id, revoked_by=current_user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
