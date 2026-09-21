"""API-level tests for POST /workspaces/{workspace_id}/market-stream-tickets.
See docs/plans/realtime-market-data-stream.md (work unit 2)."""

from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from app.api.routes import market_data as market_data_routes
from app.core.config import Settings
from app.db.session import get_db
from app.main import app
from app.market_data.infrastructure.page_access import AccessSnapshot
from app.models.workspace import AppUser
from app.security.rbac import require_viewer_role
from app.services.market_data import MarketDataAccessError
from fastapi.testclient import TestClient

client = TestClient(app)
SECRET = "test-signing-secret"

_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def _override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER


def _configure_ticket_settings(monkeypatch, **overrides) -> None:
    kwargs = {"market_stream_ticket_secret": SECRET, "market_stream_ticket_ttl_seconds": 60}
    kwargs.update(overrides)
    monkeypatch.setattr(market_data_routes, "get_settings", lambda: Settings(**kwargs))


def _stub_page_access(monkeypatch, *, resolve=None, side_effect=None) -> MagicMock:
    if side_effect:
        resolver = MagicMock(side_effect=side_effect)
    else:
        resolver = MagicMock(return_value=resolve)
    instance = MagicMock()
    instance.resolve = resolver
    monkeypatch.setattr(market_data_routes, "PageAccess", MagicMock(return_value=instance))
    monkeypatch.setattr(market_data_routes, "get_secret_store", MagicMock())
    return resolver


def test_issues_ticket_when_access_resolves(monkeypatch) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    session = MagicMock()
    session.get.return_value = MagicMock()  # workspace found
    _configure_ticket_settings(monkeypatch)
    access = AccessSnapshot(
        exchange="binance",
        symbol="BTCJPY",
        base_url="https://testnet.binance.vision",
        connection_id=uuid4(),
        account_id=uuid4(),
        secret_ref="local-encrypted://" + "0" * 32,
        credentials_updated_at=None,
        selection_updated_at=MagicMock(),
    )
    _stub_page_access(monkeypatch, resolve=access)
    _override_database(session)

    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/market-stream-tickets",
            json={"instrument_id": str(instrument_id), "timeframe": "1m"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["exchange"] == "binance"
    assert body["symbol"] == "BTCJPY"
    assert body["timeframe"] == "1m"
    claims = jwt.decode(body["ticket"], SECRET, algorithms=["HS256"])
    assert claims["workspace_id"] == str(workspace_id)


def test_rejects_when_ticket_signing_is_not_configured(monkeypatch) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    _configure_ticket_settings(monkeypatch, market_stream_ticket_secret=None)
    _override_database(MagicMock())

    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/market-stream-tickets",
            json={"instrument_id": str(instrument_id), "timeframe": "1m"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503


def test_rejects_when_access_is_unavailable(monkeypatch) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    session = MagicMock()
    session.get.return_value = MagicMock()
    _configure_ticket_settings(monkeypatch)
    _stub_page_access(
        monkeypatch,
        side_effect=MarketDataAccessError("no verified connection", "access_unavailable"),
    )
    _override_database(session)

    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/market-stream-tickets",
            json={"instrument_id": str(instrument_id), "timeframe": "1m"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_rejects_unknown_workspace_before_touching_page_access(monkeypatch) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    session = MagicMock()
    session.get.return_value = None
    _configure_ticket_settings(monkeypatch)
    resolver = _stub_page_access(monkeypatch, resolve=None)
    _override_database(session)

    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/market-stream-tickets",
            json={"instrument_id": str(instrument_id), "timeframe": "1m"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    resolver.assert_not_called()


@pytest.mark.parametrize("timeframe", ["2m", "", "1M"])
def test_rejects_unsupported_timeframe(monkeypatch, timeframe) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    _configure_ticket_settings(monkeypatch)
    _override_database(MagicMock())

    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/market-stream-tickets",
            json={"instrument_id": str(instrument_id), "timeframe": timeframe},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
