from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.catalog import Exchange, Market, Workspace
from fastapi.testclient import TestClient

client = TestClient(app)


def override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session


def test_list_workspaces() -> None:
    now = datetime.now(UTC)
    workspace = Workspace(id=uuid4(), name="Personal", status="active")
    workspace.created_at = now
    workspace.updated_at = now
    session = MagicMock()
    session.scalars.return_value.all.return_value = [workspace]
    override_database(session)
    try:
        response = client.get("/api/v1/workspaces")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Personal"


def test_list_reference_catalog() -> None:
    exchange = Exchange(id=uuid4(), code="oanda", name="OANDA", status="active")
    market = Market(
        id=uuid4(),
        code="foreign_fx_spot",
        asset_class="foreign_fx",
        product_type="fx_spot",
        settlement_type="rolling_spot",
    )
    session = MagicMock()
    session.scalars.return_value.all.side_effect = [[exchange], [market]]
    override_database(session)
    try:
        exchanges_response = client.get("/api/v1/exchanges")
        markets_response = client.get("/api/v1/markets")
    finally:
        app.dependency_overrides.clear()

    assert exchanges_response.status_code == 200
    assert exchanges_response.json()[0]["code"] == "oanda"
    assert markets_response.status_code == 200
    assert markets_response.json()[0]["code"] == "foreign_fx_spot"


def test_get_missing_workspace_returns_404() -> None:
    session = MagicMock()
    session.get.return_value = None
    override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
