from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.catalog import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Workspace,
)
from app.security.auth import require_owner
from fastapi.testclient import TestClient

client = TestClient(app)


def test_workspace_account_exposes_unverified_connection_state() -> None:
    workspace_id = uuid4()
    exchange = Exchange(id=uuid4(), code="binance", name="Binance", status="active")
    connection = ExchangeConnection(
        id=uuid4(),
        workspace_id=workspace_id,
        exchange_id=exchange.id,
        label="Binance Spot Testnet",
        environment="testnet",
        api_base_url="https://testnet.binance.vision",
        status="verifying",
    )
    account = ExternalAccount(
        id=uuid4(),
        connection_id=connection.id,
        external_account_ref_encrypted="encrypted",
        external_account_ref_hash="hash",
        external_account_ref_masked="****-key",
        environment="testnet",
        currency="MULTI",
        status="active",
    )
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.execute.return_value.all.return_value = [(account, connection, exchange, None)]
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_owner] = lambda: "test-owner"
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["connection_status"] == "verifying"
    assert response.json()[0]["selected"] is False
