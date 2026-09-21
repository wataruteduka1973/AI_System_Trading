"""Unit 3 (docs/plans/horizon5-implementation-plan.md 557行目 テスト方針):
`/auth/login` redirects to the IdP's authorization URL, `/auth/callback`'s
success/failure paths (state mismatch, IdP rejection, invalid ID token,
disabled user), and `/auth/me`'s auth requirement.

`oidc.py`'s own HTTP-calling functions (fetch_discovery_document,
exchange_code_for_tokens) are already covered against a mocked HTTP layer in
tests/test_oidc_client.py; here they are monkeypatched directly so these
tests are about the route wiring (config lookup, cookie handling, status
codes), not re-testing PKCE/discovery/token-exchange mechanics.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.workspace import AppUser
from app.security import oidc
from app.security.rbac import require_authenticated_user
from fastapi.testclient import TestClient
from pydantic import SecretStr

client = TestClient(app, follow_redirects=False)

DISCOVERY = oidc.DiscoveryDocument(
    authorization_endpoint="https://idp.example.com/authorize",
    token_endpoint="https://idp.example.com/token",
    jwks_uri="https://idp.example.com/jwks",
    issuer="https://idp.example.com",
)


def _configure_oidc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.com")
    monkeypatch.setattr(settings, "oidc_client_id", "client-1")
    monkeypatch.setattr(settings, "oidc_client_secret", SecretStr("secret-1"))
    monkeypatch.setattr(settings, "session_signing_secret", SecretStr("test-session-secret"))
    monkeypatch.setattr(settings, "app_env", "local")


async def _fake_discovery(issuer: str) -> oidc.DiscoveryDocument:
    return DISCOVERY


# ---- /auth/login ----


def test_login_returns_503_when_oidc_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oidc_issuer", None)
    response = client.get("/api/v1/auth/login")
    assert response.status_code == 503


def test_login_redirects_to_the_authorization_url_and_sets_the_state_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setattr(auth_routes.oidc, "fetch_discovery_document", _fake_discovery)

    response = client.get("/api/v1/auth/login")

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://idp.example.com/authorize?")
    assert "code_challenge_method=S256" in location
    assert "oidc_state" in response.cookies


# ---- /auth/callback ----


def test_callback_returns_400_for_a_state_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_pending_login",
        lambda token, *, secret: oidc.PendingLogin(
            state="expected-state", code_verifier="v", nonce="n"
        ),
    )

    response = client.get(
        "/api/v1/auth/callback",
        params={"code": "auth-code", "state": "mismatched-state"},
        cookies={"oidc_state": "whatever"},
    )

    assert response.status_code == 400


def test_callback_returns_400_when_the_oidc_state_cookie_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_oidc(monkeypatch)
    response = client.get("/api/v1/auth/callback", params={"code": "auth-code", "state": "s"})
    assert response.status_code == 400


def test_callback_returns_401_when_the_idp_rejects_token_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_pending_login",
        lambda token, *, secret: oidc.PendingLogin(state="s", code_verifier="v", nonce="n"),
    )
    monkeypatch.setattr(auth_routes.oidc, "fetch_discovery_document", _fake_discovery)

    async def _fail_exchange(*args: object, **kwargs: object) -> dict[str, str]:
        raise oidc.OidcError(
            "token_exchange_failed", "IdP rejected the authorization code exchange"
        )

    monkeypatch.setattr(auth_routes.oidc, "exchange_code_for_tokens", _fail_exchange)

    response = client.get(
        "/api/v1/auth/callback",
        params={"code": "bad-code", "state": "s"},
        cookies={"oidc_state": "whatever"},
    )

    assert response.status_code == 401


def test_callback_returns_401_for_an_invalid_id_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_pending_login",
        lambda token, *, secret: oidc.PendingLogin(state="s", code_verifier="v", nonce="n"),
    )
    monkeypatch.setattr(auth_routes.oidc, "fetch_discovery_document", _fake_discovery)

    async def _exchange(*args: object, **kwargs: object) -> dict[str, str]:
        return {"id_token": "not-a-valid-jwt"}

    monkeypatch.setattr(auth_routes.oidc, "exchange_code_for_tokens", _exchange)

    def _fail_verify(*args: object, **kwargs: object) -> dict[str, object]:
        raise oidc.OidcError("id_token_invalid", "ID token failed verification")

    monkeypatch.setattr(auth_routes.oidc, "verify_id_token", _fail_verify)

    response = client.get(
        "/api/v1/auth/callback",
        params={"code": "auth-code", "state": "s"},
        cookies={"oidc_state": "whatever"},
    )

    assert response.status_code == 401


def test_callback_returns_403_for_a_disabled_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_pending_login",
        lambda token, *, secret: oidc.PendingLogin(state="s", code_verifier="v", nonce="n"),
    )
    monkeypatch.setattr(auth_routes.oidc, "fetch_discovery_document", _fake_discovery)

    async def _exchange(*args: object, **kwargs: object) -> dict[str, str]:
        return {"id_token": "opaque-token"}

    monkeypatch.setattr(auth_routes.oidc, "exchange_code_for_tokens", _exchange)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_id_token",
        lambda *a, **k: {"sub": "subject-1", "email": "disabled@example.com", "name": "Disabled"},
    )

    disabled_user = AppUser(
        id=uuid4(), email="disabled@example.com", display_name="Disabled", status="disabled"
    )
    session = MagicMock()
    session.scalar.return_value = disabled_user
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = client.get(
            "/api/v1/auth/callback",
            params={"code": "auth-code", "state": "s"},
            cookies={"oidc_state": "whatever"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_callback_succeeds_for_an_existing_user_and_sets_the_session_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_pending_login",
        lambda token, *, secret: oidc.PendingLogin(state="s", code_verifier="v", nonce="n"),
    )
    monkeypatch.setattr(auth_routes.oidc, "fetch_discovery_document", _fake_discovery)

    async def _exchange(*args: object, **kwargs: object) -> dict[str, str]:
        return {"id_token": "opaque-token"}

    monkeypatch.setattr(auth_routes.oidc, "exchange_code_for_tokens", _exchange)
    monkeypatch.setattr(
        auth_routes.oidc,
        "verify_id_token",
        lambda *a, **k: {"sub": "subject-1", "email": "active@example.com", "name": "Active"},
    )

    existing_user = AppUser(
        id=uuid4(), email="active@example.com", display_name="Active", status="active"
    )
    session = MagicMock()
    session.scalar.return_value = existing_user
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = client.get(
            "/api/v1/auth/callback",
            params={"code": "auth-code", "state": "s"},
            cookies={"oidc_state": "whatever"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert "session" in response.cookies
    set_cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in set_cookie  # oidc_state cookie is cleared in the same response
    session.commit.assert_called_once()


# ---- /auth/me ----


def test_me_requires_authentication() -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_the_current_user_and_memberships() -> None:
    user = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")
    membership = MagicMock(workspace_id=uuid4(), role="owner")
    workspace = MagicMock()
    workspace.name = "My Workspace"
    session = MagicMock()
    session.execute.return_value.all.return_value = [(membership, workspace)]

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_user] = lambda: user
    try:
        response = client.get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "me@example.com"
    assert body["memberships"][0]["role"] == "owner"
    assert body["memberships"][0]["workspace_name"] == "My Workspace"


# ---- /auth/logout ----


def test_logout_clears_the_session_cookie() -> None:
    response = client.post("/api/v1/auth/logout", cookies={"session": "whatever-token"})
    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie.startswith('session=""')
    assert "Max-Age=0" in set_cookie
