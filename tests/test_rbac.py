"""Unit 3/4 (docs/plans/horizon5-implementation-plan.md): `require_authenticated_user`,
`require_workspace_role`, and `require_any_workspace_owner` exercised directly
(no HTTP layer, no TestClient) with a MagicMock Session -- matching this
codebase's existing convention for testing dependency/use-case functions.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.workspace import AppUser, UserMembership
from app.security import rbac
from fastapi import HTTPException


def _user(**overrides: object) -> AppUser:
    defaults: dict[str, object] = dict(
        id=uuid4(), email="a@example.com", display_name="A", status="active"
    )
    defaults.update(overrides)
    return AppUser(**defaults)


def _membership(role: str, **overrides: object) -> UserMembership:
    defaults: dict[str, object] = dict(workspace_id=uuid4(), user_id=uuid4(), role=role)
    defaults.update(overrides)
    return UserMembership(**defaults)


# ---- require_authenticated_user ----


def test_require_authenticated_user_rejects_a_missing_cookie() -> None:
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        rbac.require_authenticated_user(db, session=None)
    assert exc.value.status_code == 401


def test_require_authenticated_user_rejects_a_disabled_account(monkeypatch) -> None:
    db = MagicMock()
    user = _user(status="disabled")
    db.get.return_value = user
    monkeypatch.setattr(
        rbac.settings, "session_signing_secret", MagicMock(get_secret_value=lambda: "s")
    )
    monkeypatch.setattr(
        rbac,
        "verify_session_token",
        lambda token, *, secret: MagicMock(app_user_id=user.id, issued_at=datetime.now(UTC)),
    )
    with pytest.raises(HTTPException) as exc:
        rbac.require_authenticated_user(db, session="token")
    assert exc.value.status_code == 401


def test_require_authenticated_user_rejects_a_revoked_session(monkeypatch) -> None:
    db = MagicMock()
    user = _user()
    db.get.return_value = user
    monkeypatch.setattr(
        rbac.settings, "session_signing_secret", MagicMock(get_secret_value=lambda: "s")
    )
    monkeypatch.setattr(
        rbac,
        "verify_session_token",
        lambda token, *, secret: MagicMock(app_user_id=user.id, issued_at=datetime.now(UTC)),
    )
    monkeypatch.setattr(rbac, "is_session_revoked", lambda db, *, user_id, issued_at: True)
    with pytest.raises(HTTPException) as exc:
        rbac.require_authenticated_user(db, session="token")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Session revoked"


def test_require_authenticated_user_succeeds(monkeypatch) -> None:
    db = MagicMock()
    user = _user()
    db.get.return_value = user
    monkeypatch.setattr(
        rbac.settings, "session_signing_secret", MagicMock(get_secret_value=lambda: "s")
    )
    monkeypatch.setattr(
        rbac,
        "verify_session_token",
        lambda token, *, secret: MagicMock(app_user_id=user.id, issued_at=datetime.now(UTC)),
    )
    monkeypatch.setattr(rbac, "is_session_revoked", lambda db, *, user_id, issued_at: False)
    assert rbac.require_authenticated_user(db, session="token") is user


# ---- require_workspace_role (via the shared singletons) ----


def test_require_viewer_role_rejects_a_non_member() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as exc:
        rbac.require_viewer_role(uuid4(), db, _user())
    assert exc.value.status_code == 404  # not 403: existence is not disclosed


def test_require_owner_role_rejects_a_viewer_member() -> None:
    db = MagicMock()
    db.scalar.return_value = _membership("viewer")
    with pytest.raises(HTTPException) as exc:
        rbac.require_owner_role(uuid4(), db, _user())
    assert exc.value.status_code == 403


def test_require_operator_role_accepts_an_operator_member() -> None:
    db = MagicMock()
    user = _user()
    db.scalar.return_value = _membership("operator")
    assert rbac.require_operator_role(uuid4(), db, user) is user


def test_require_viewer_role_accepts_an_owner_member() -> None:
    """Role severity is a minimum, not an exact match -- owner satisfies a
    viewer-or-above requirement too."""
    db = MagicMock()
    user = _user()
    db.scalar.return_value = _membership("owner")
    assert rbac.require_viewer_role(uuid4(), db, user) is user


def test_require_viewer_operator_owner_are_distinct_objects() -> None:
    """The whole point of exposing module-level singletons (rather than each
    route module calling require_workspace_role(...) itself) is that
    app.dependency_overrides can target one shared object per role -- confirm
    they really are three distinct callables, not the same one three times."""
    assert rbac.require_viewer_role is not rbac.require_operator_role
    assert rbac.require_operator_role is not rbac.require_owner_role


# ---- require_any_workspace_owner ----


def test_require_any_workspace_owner_accepts_ownership_of_any_workspace() -> None:
    db = MagicMock()
    user = _user()
    db.scalar.return_value = uuid4()  # some workspace_id the user owns
    assert rbac.require_any_workspace_owner(db, user) is user


def test_require_any_workspace_owner_rejects_no_ownership() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as exc:
        rbac.require_any_workspace_owner(db, _user())
    assert exc.value.status_code == 403
