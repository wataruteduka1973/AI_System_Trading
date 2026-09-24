"""Unit 3.9/4.4 (docs/plans/horizon5-implementation-plan.md): the
/auth/sessions/revoke (self) and /auth/users/{user_id}/revoke-sessions
(admin) HTTP endpoints. `app.security.session.revoke_sessions` itself is
already covered by tests/test_session_token.py; this file is about who is
allowed to call which endpoint.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.workspace import AppUser
from app.security.rbac import require_any_workspace_owner, require_authenticated_user
from fastapi.testclient import TestClient

client = TestClient(app)
_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def _override_database(session: MagicMock, *, as_owner: bool = False) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_user] = lambda: _TEST_USER
    if as_owner:
        app.dependency_overrides[require_any_workspace_owner] = lambda: _TEST_USER


def test_revoke_own_sessions_clears_the_session_cookie() -> None:
    session = MagicMock()
    session.get.return_value = None  # revoke_sessions: no existing SessionRevocation row
    _override_database(session)
    try:
        response = client.post(
            "/api/v1/auth/sessions/revoke", cookies={"session": "whatever-token"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie.startswith('session=""')
    assert "Max-Age=0" in set_cookie
    session.add.assert_called_once()  # a new SessionRevocation row was created


def test_revoke_user_sessions_requires_workspace_ownership() -> None:
    session = MagicMock()
    session.scalar.return_value = None  # require_any_workspace_owner's real check: no ownership
    _override_database(session, as_owner=False)
    try:
        response = client.post(f"/api/v1/auth/users/{uuid4()}/revoke-sessions")
    finally:
        app.dependency_overrides.clear()

    # require_any_workspace_owner was not overridden, so its real
    # require_authenticated_user sub-dependency runs -- but that in turn
    # *was* overridden, so this reaches the real ownership check, which
    # queries the (mocked, empty) session and rejects with 403.
    assert response.status_code == 403


def test_revoke_user_sessions_succeeds_for_an_owner() -> None:
    target_user = AppUser(id=uuid4(), email="b@example.com", display_name="B", status="active")
    session = MagicMock()
    session.get.return_value = target_user
    # The shared-workspace-membership query (see the route's own IDOR-fix
    # comment) -- an unconfigured MagicMock().scalar() would return a truthy
    # MagicMock by default regardless of this check's real logic, so this must
    # be set explicitly or the test passes for the wrong reason.
    session.scalar.return_value = uuid4()  # a workspace_id both users share
    _override_database(session, as_owner=True)
    try:
        response = client.post(f"/api/v1/auth/users/{target_user.id}/revoke-sessions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204


def test_revoke_user_sessions_rejects_an_unrelated_workspace_owner() -> None:
    """Regression test (/code-review IDOR finding): being an Owner of *some*
    workspace is not enough -- with self-service workspace creation, that is
    trivially true of every user. The target must actually be a member of a
    workspace the caller owns."""
    target_user = AppUser(id=uuid4(), email="b@example.com", display_name="B", status="active")
    session = MagicMock()
    session.get.return_value = target_user
    session.scalar.return_value = None  # no workspace shared with the target
    _override_database(session, as_owner=True)
    try:
        response = client.post(f"/api/v1/auth/users/{target_user.id}/revoke-sessions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_revoke_user_sessions_returns_404_for_an_unknown_user() -> None:
    session = MagicMock()
    session.get.return_value = None
    _override_database(session, as_owner=True)
    try:
        response = client.post(f"/api/v1/auth/users/{uuid4()}/revoke-sessions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
