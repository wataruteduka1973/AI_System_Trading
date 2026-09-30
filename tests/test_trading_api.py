"""Bot management API (app/api/routes/trading.py): trading-accounts and bots
resources. Mirrors tests/test_trading_halts_api.py's MagicMock-session,
dependency-override style. `ensure_dummy_bot`'s own logic is covered in
tests/test_dummy_pipeline.py; these tests are about the HTTP layer (role
gating, 404/409 mapping, request/response shapes) wired on top of it.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.connections import ExchangeConnection
from app.models.instruments import Instrument
from app.models.strategy import BotRun, Signal, TradingBot
from app.models.trading import LedgerTransaction, TradingAccount
from app.models.workspace import AppUser
from app.security.rbac import require_operator_role, require_viewer_role
from app.trading.application import bot_lifecycle
from app.trading.application.bot_lifecycle import BotLifecycleError, BotStateConflictError
from fastapi.testclient import TestClient

client = TestClient(app)
_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def _override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    app.dependency_overrides[require_operator_role] = lambda: _TEST_USER


def _connection(**overrides: object) -> ExchangeConnection:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        exchange_id=uuid4(),
        label="c",
        environment="practice",
        api_base_url="https://x",
        status="verified",
    )
    defaults.update(overrides)
    return ExchangeConnection(**defaults)


def _account(**overrides: object) -> TradingAccount:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        connection_id=uuid4(),
        mode="paper",
        base_currency="JPY",
        status="active",
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return TradingAccount(**defaults)


def _bot(**overrides: object) -> TradingBot:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        name="bot-1",
        execution_mode="paper",
        strategy_mode="technical",
        connection_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        desired_state="stopped",
        actual_state="stopped",
        live_trading_enabled=False,
        version=1,
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return TradingBot(**defaults)


def _bot_run(**overrides: object) -> BotRun:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        bot_id=uuid4(),
        status="running",
        code_version="dummy-pipeline-0.1",
        started_at=datetime.now(UTC),
        stopped_at=None,
        stop_reason=None,
        heartbeat_at=None,
    )
    defaults.update(overrides)
    return BotRun(**defaults)


# ---- trading-accounts ----


def test_create_trading_account() -> None:
    workspace_id = uuid4()
    connection = _connection(workspace_id=workspace_id)
    session = MagicMock()
    session.scalar.return_value = connection

    def set_generated(account: TradingAccount) -> None:
        account.id = uuid4()
        account.status = "active"
        account.created_at = datetime.now(UTC)

    session.refresh.side_effect = set_generated
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/trading-accounts",
            json={"connection_id": str(connection.id), "base_currency": "JPY"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "paper"
    assert body["base_currency"] == "JPY"


def test_create_trading_account_404_for_connection_in_another_workspace() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{uuid4()}/trading-accounts",
            json={"connection_id": str(uuid4()), "base_currency": "JPY"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_list_trading_accounts() -> None:
    workspace_id = uuid4()
    account = _account(workspace_id=workspace_id)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [account]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/trading-accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["base_currency"] == "JPY"


def test_create_deposit_404_for_account_in_another_workspace() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{uuid4()}/trading-accounts/{uuid4()}/deposits",
            json={"amount": "1000", "asset": "JPY"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_create_deposit_seeds_a_ledger_transaction() -> None:
    workspace_id = uuid4()
    account = _account(workspace_id=workspace_id)
    session = MagicMock()
    session.scalar.return_value = account

    def set_generated(transaction: LedgerTransaction) -> None:
        transaction.id = uuid4()
        transaction.occurred_at = datetime.now(UTC)

    session.refresh.side_effect = set_generated
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/trading-accounts/{account.id}/deposits",
            json={"amount": "1000", "asset": "JPY"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["account_id"] == str(account.id)
    assert body["description"] == "paper account seed deposit"


# ---- bots ----


def test_create_trading_bot_409_when_name_already_taken() -> None:
    workspace_id = uuid4()
    session = MagicMock()
    session.scalar.return_value = _bot(workspace_id=workspace_id)  # duplicate-name pre-check
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/bots",
            json={
                "name": "bot-1",
                "account_id": str(uuid4()),
                "instrument_id": str(uuid4()),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_create_trading_bot_404_for_account_in_another_workspace() -> None:
    workspace_id = uuid4()
    session = MagicMock()
    session.scalar.side_effect = [None, None]  # name pre-check, then _get_account
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/bots",
            json={
                "name": "bot-1",
                "account_id": str(uuid4()),
                "instrument_id": str(uuid4()),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_create_trading_bot() -> None:
    workspace_id = uuid4()
    account = _account(workspace_id=workspace_id)
    instrument = Instrument(
        id=uuid4(),
        exchange_id=uuid4(),
        market_id=uuid4(),
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_scale=2,
        quantity_scale=6,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.000001"),
    )
    session = MagicMock()
    # 1) route's own duplicate-name pre-check, 2) _get_account, then
    # ensure_dummy_bot's own 5 lookups (Strategy/StrategyVersion/RiskProfile/
    # RiskProfileVersion/TradingBot-by-name), all missing.
    session.scalar.side_effect = [None, account, None, None, None, None, None]
    session.get.return_value = instrument

    def set_generated(bot: TradingBot) -> None:
        bot.id = uuid4()
        bot.desired_state = "stopped"
        bot.actual_state = "stopped"
        bot.live_trading_enabled = False
        bot.version = 1
        bot.created_at = datetime.now(UTC)

    session.refresh.side_effect = set_generated
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/bots",
            json={
                "name": "bot-1",
                "account_id": str(account.id),
                "instrument_id": str(instrument.id),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "bot-1"
    assert body["desired_state"] == "stopped"  # created, not started


def test_list_trading_bots() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [bot]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/bots")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["name"] == "bot-1"


def test_get_trading_bot_404_for_bot_in_another_workspace() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{uuid4()}/bots/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_get_latest_bot_run_404_when_bot_has_never_been_started() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id)
    session = MagicMock()
    # _get_bot finds the bot, then the BotRun lookup finds nothing.
    session.scalar.side_effect = [bot, None]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/latest-run")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_get_latest_bot_run_without_a_signal_yet() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id)
    run = _bot_run(bot_id=bot.id, status="running")
    session = MagicMock()
    session.scalar.side_effect = [bot, run, None]  # bot, latest BotRun, no Signal yet
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/latest-run")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["latest_signal"] is None


def test_get_latest_bot_run_includes_the_latest_signal() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id)
    run = _bot_run(bot_id=bot.id, status="running")
    signal = Signal(
        id=uuid4(),
        workspace_id=workspace_id,
        bot_run_id=run.id,
        candle_id=uuid4(),
        strategy_version_id=bot.strategy_version_id,
        action="buy",
        rationale={},
        input_checksum="x",
        created_at=datetime.now(UTC),
    )
    session = MagicMock()
    session.scalar.side_effect = [bot, run, signal]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/latest-run")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["latest_signal"]["action"] == "buy"
    assert body["latest_signal"]["id"] == str(signal.id)


def test_start_trading_bot() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id, desired_state="stopped")
    run = _bot_run(bot_id=bot.id)
    session = MagicMock()
    session.scalar.return_value = bot
    _override_database(session)

    original_start = bot_lifecycle.start_bot
    bot_lifecycle.start_bot = MagicMock(return_value=run)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/start")
    finally:
        bot_lifecycle.start_bot = original_start
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_start_trading_bot_maps_state_conflict_to_409() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id, desired_state="running")
    session = MagicMock()
    session.scalar.return_value = bot
    _override_database(session)

    original_start = bot_lifecycle.start_bot
    bot_lifecycle.start_bot = MagicMock(side_effect=BotStateConflictError("Bot is already running"))
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/start")
    finally:
        bot_lifecycle.start_bot = original_start
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_start_trading_bot_maps_other_lifecycle_errors_to_422() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id)
    session = MagicMock()
    session.scalar.return_value = bot
    _override_database(session)

    original_start = bot_lifecycle.start_bot
    bot_lifecycle.start_bot = MagicMock(
        side_effect=BotLifecycleError("startup_validation_failed_secrets", "no secrets")
    )
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/start")
    finally:
        bot_lifecycle.start_bot = original_start
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_pause_trading_bot() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id, desired_state="running")
    run = _bot_run(bot_id=bot.id, status="paused")
    session = MagicMock()
    session.scalar.return_value = bot
    _override_database(session)

    original = bot_lifecycle.pause_bot
    bot_lifecycle.pause_bot = MagicMock(return_value=run)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/pause")
    finally:
        bot_lifecycle.pause_bot = original
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "paused"


def test_resume_trading_bot() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id, desired_state="paused")
    run = _bot_run(bot_id=bot.id, status="running")
    session = MagicMock()
    session.scalar.return_value = bot
    _override_database(session)

    original = bot_lifecycle.resume_bot
    bot_lifecycle.resume_bot = MagicMock(return_value=run)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/resume")
    finally:
        bot_lifecycle.resume_bot = original
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_stop_trading_bot() -> None:
    workspace_id = uuid4()
    bot = _bot(workspace_id=workspace_id, desired_state="running")
    run = _bot_run(bot_id=bot.id, status="stopped", stop_reason="stop command")
    session = MagicMock()
    session.scalar.return_value = bot
    _override_database(session)

    original = bot_lifecycle.stop_bot
    bot_lifecycle.stop_bot = MagicMock(return_value=run)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot.id}/stop")
    finally:
        bot_lifecycle.stop_bot = original
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


def test_start_trading_bot_requires_operator_role() -> None:
    workspace_id, bot_id = uuid4(), uuid4()
    session = MagicMock()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    # require_operator_role intentionally left un-overridden: with no session
    # cookie, it falls through to require_authenticated_user's real 401.
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/bots/{bot_id}/start")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
