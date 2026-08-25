from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.exchanges.oanda import OandaInstrumentRules, get_oanda_practice_client
from app.main import app
from app.models.catalog import (
    AuditLog,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    Market,
    Workspace,
    WorkspaceAccountSelection,
)
from app.security.auth import require_owner
from app.services.secrets import get_secret_store
from fastapi.testclient import TestClient

client = TestClient(app)


def override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_owner] = lambda: "test-owner"


def test_sync_selected_oanda_instrument_without_disclosing_secrets() -> None:
    now = datetime.now(UTC)
    workspace_id = uuid4()
    exchange = Exchange(id=uuid4(), code="oanda", name="OANDA", status="active")
    market = Market(
        id=uuid4(),
        code="foreign_fx_spot",
        asset_class="foreign_fx",
        product_type="fx_spot",
        settlement_type="rolling_spot",
    )
    connection = ExchangeConnection(
        id=uuid4(),
        workspace_id=workspace_id,
        exchange_id=exchange.id,
        label="OANDA practice",
        environment="practice",
        api_base_url="https://api-fxpractice.oanda.com",
        secret_ref="local-encrypted://0123456789abcdef0123456789abcdef",
        status="verified",
    )
    account = ExternalAccount(
        id=uuid4(),
        connection_id=connection.id,
        external_account_ref_encrypted="encrypted-account-reference",
        external_account_ref_hash="account-hash",
        external_account_ref_masked="****8001",
        environment="practice",
        currency="JPY",
        status="active",
    )
    selection = WorkspaceAccountSelection(
        workspace_id=workspace_id,
        exchange_id=exchange.id,
        external_account_id=account.id,
    )
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.execute.return_value.all.return_value = [(selection, account, connection, exchange)]
    session.scalar.side_effect = [market, None]

    def set_instrument_id() -> None:
        for call in session.add.call_args_list:
            if isinstance(call.args[0], Instrument):
                call.args[0].id = uuid4()
                call.args[0].created_at = now

    session.flush.side_effect = set_instrument_id
    secret_store = MagicMock()
    secret_store.get.return_value = {"token": "private-token"}
    secret_store.decrypt_text.return_value = "private-account-id"
    oanda_client = MagicMock()
    oanda_client.get_instrument_rules = AsyncMock(
        return_value=OandaInstrumentRules(
            symbol="USD_JPY",
            base_asset="USD",
            quote_asset="JPY",
            price_scale=3,
            quantity_scale=0,
            tick_size=Decimal("0.001"),
            step_size=Decimal("1"),
            min_quantity=Decimal("1"),
            max_quantity=Decimal("100000000"),
            instrument_type="CURRENCY",
        )
    )
    override_database(session)
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    app.dependency_overrides[get_oanda_practice_client] = lambda: oanda_client
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/instruments/sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["instruments"][0]["symbol"] == "USD_JPY"
    assert response.json()["instruments"][0]["tick_size"] == "0.001"
    assert "private-token" not in response.text
    assert "private-account-id" not in response.text
    audit = next(
        call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)
    )
    assert audit.after_data == {
        "exchange_code": "oanda",
        "symbol": "USD_JPY",
        "outcome": "succeeded",
    }


def test_sync_requires_selected_verified_account() -> None:
    workspace_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.execute.return_value.all.return_value = []
    override_database(session)
    app.dependency_overrides[get_secret_store] = lambda: MagicMock()
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/instruments/sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
