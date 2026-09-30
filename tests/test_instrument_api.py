from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.api.routes import instruments as instruments_routes
from app.db.session import get_db
from app.exchanges.oanda import OandaInstrumentRules, get_oanda_practice_client
from app.main import app
from app.models.audit import AuditLog
from app.models.connections import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Market,
    WorkspaceAccountSelection,
)
from app.models.instruments import Instrument
from app.models.workspace import AppUser, Workspace
from app.security.rbac import require_operator_role, require_viewer_role
from app.services.secrets import get_secret_store
from fastapi.testclient import TestClient

client = TestClient(app)

_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    app.dependency_overrides[require_operator_role] = lambda: _TEST_USER


def test_sync_selected_oanda_instrument_without_disclosing_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Auto-start (2026-09-26, per user decision) is exercised by its own dedicated
    # test below; mocking it out here keeps this test focused on the sync response
    # itself and avoids needing to simulate its much deeper db.scalar/execute chain.
    monkeypatch.setattr(instruments_routes, "_auto_start_collection", MagicMock())
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
    # 2026-09-26 (per user decision): a successful sync must not require a
    # separate manual "get past year" click before an instrument has any data.
    instruments_routes._auto_start_collection.assert_called_once()


def test_auto_start_collection_enables_all_timeframes_and_backfills_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.market_data.application import use_cases as market_data_application

    db = MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    subscription_calls: list[dict[str, object]] = []
    backfill_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        market_data_application,
        "update_subscriptions",
        lambda db_, ws, inst, *, enabled, validate_configuration, timeframe=None: (
            subscription_calls.append(
                {"enabled": enabled, "timeframe": timeframe, "instrument_id": inst}
            )
        ),
    )
    monkeypatch.setattr(
        market_data_application,
        "enqueue_backfill",
        lambda db_, ws, command, validate_configuration, *, trigger_type: backfill_calls.append(
            (command.instrument_id, command.timeframe, command.days, trigger_type)
        ),
    )

    instruments_routes._auto_start_collection(db, workspace_id, instrument_id)

    assert subscription_calls == [
        {"enabled": True, "timeframe": None, "instrument_id": instrument_id}
    ]
    assert backfill_calls == [
        (instrument_id, frame, 365, "automatic")
        for frame in market_data_application.SUPPORTED_TIMEFRAMES
    ]


def test_auto_start_collection_skips_an_already_overlapping_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.market_data.application import use_cases as market_data_application

    db = MagicMock()
    monkeypatch.setattr(market_data_application, "update_subscriptions", MagicMock())
    monkeypatch.setattr(
        market_data_application,
        "enqueue_backfill",
        MagicMock(
            side_effect=market_data_application.MarketDataApplicationError(
                "overlapping_backfill", "already running"
            )
        ),
    )

    instruments_routes._auto_start_collection(db, uuid4(), uuid4())  # does not raise


def test_auto_start_collection_reraises_other_backfill_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.market_data.application import use_cases as market_data_application

    db = MagicMock()
    monkeypatch.setattr(market_data_application, "update_subscriptions", MagicMock())
    monkeypatch.setattr(
        market_data_application,
        "enqueue_backfill",
        MagicMock(
            side_effect=market_data_application.MarketDataApplicationError(
                "credentials_missing", "no credentials"
            )
        ),
    )

    with pytest.raises(market_data_application.MarketDataApplicationError):
        instruments_routes._auto_start_collection(db, uuid4(), uuid4())


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
