from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.catalog import Exchange, Market, Workspace
from app.security.auth import require_owner
from app.services.secrets import get_secret_store
from fastapi.testclient import TestClient

client = TestClient(app)


def override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_owner] = lambda: "test-owner"


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


def test_create_connection_stores_credentials_outside_database() -> None:
    workspace_id = uuid4()
    exchange = Exchange(id=uuid4(), code="oanda", name="OANDA", status="active")
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = exchange
    secret_store = MagicMock()
    secret_store.put.return_value = "local-encrypted://0123456789abcdef0123456789abcdef"

    def set_generated_values(connection: object) -> None:
        connection.id = uuid4()
        connection.last_verified_at = None

    session.refresh.side_effect = set_generated_values
    override_database(session)
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/connections",
            json={
                "exchange_code": "oanda",
                "label": "OANDA practice",
                "environment": "practice",
                "api_base_url": "https://api-fxpractice.oanda.com",
                "credentials": {"token": "sensitive-token"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert "sensitive-token" not in response.text
    secret_store.put.assert_called_once_with({"token": "sensitive-token"})
    saved_connection = session.add.call_args.args[0]
    assert saved_connection.secret_ref.startswith("local-encrypted://")


def test_disable_connection() -> None:
    workspace_id = uuid4()
    connection = MagicMock()
    connection.id = uuid4()
    connection.workspace_id = workspace_id
    connection.exchange_id = uuid4()
    connection.label = "OANDA practice"
    connection.environment = "practice"
    connection.api_base_url = "https://api-fxpractice.oanda.com"
    connection.status = "verified"
    connection.capabilities = {}
    connection.last_verified_at = None
    session = MagicMock()
    session.scalar.return_value = connection
    override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/connections/{connection.id}/disable"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
