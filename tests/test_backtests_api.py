"""Backtest API (app/api/routes/backtests.py, Horizon 4 API/UI task). Mirrors
tests/test_trading_api.py's MagicMock-session, dependency-override style.
`run_backtest_for_workspace`'s own orchestration is covered in
tests/test_backtest_provisioning.py; these tests are about the HTTP layer
(instrument-access gating reused from market_data.py, 404/409/422 mapping,
role gating, request/response shapes).
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.api.routes import backtests as backtests_routes
from app.db.session import get_db
from app.main import app
from app.models.backtest import BacktestRun, BacktestTrade
from app.models.instruments import Instrument
from app.models.workspace import AppUser
from app.security.rbac import require_operator_role, require_viewer_role
from app.trading.application.backtest_provisioning import BacktestProvisioningError
from fastapi.testclient import TestClient

client = TestClient(app)
_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def _override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    app.dependency_overrides[require_operator_role] = lambda: _TEST_USER


def _instrument(**overrides: object) -> Instrument:
    defaults: dict[str, object] = dict(
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
    defaults.update(overrides)
    return Instrument(**defaults)


def _backtest_run(**overrides: object) -> BacktestRun:
    now = datetime.now(UTC)
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        dataset_snapshot_id=uuid4(),
        parameters={},
        code_version="dummy-pipeline-0.1",
        status="succeeded",
        summary_metrics={"metrics": {"net_pnl": "0"}},
        started_at=now,
        finished_at=now,
        created_at=now,
    )
    defaults.update(overrides)
    return BacktestRun(**defaults)


_BODY = {
    "instrument_id": str(uuid4()),
    "timeframe": "1m",
    "from_time": "2026-09-01T00:00:00Z",
    "to_time": "2026-09-02T00:00:00Z",
    "initial_equity": "10000",
}


def test_create_backtest_404_when_workspace_not_found() -> None:
    session = MagicMock()
    session.get.return_value = None  # _require_workspace
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/backtests", json=_BODY)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_create_backtest_409_when_instrument_is_not_accessible() -> None:
    session = MagicMock()
    session.get.return_value = MagicMock()  # workspace exists
    session.scalar.return_value = None  # instrument-access join finds nothing
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/backtests", json=_BODY)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_create_backtest_404_when_instrument_row_is_missing() -> None:
    session = MagicMock()
    session.get.side_effect = [MagicMock(), None]  # workspace found, then instrument missing
    session.scalar.return_value = "accessible"
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/backtests", json=_BODY)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_create_backtest_422_when_provisioning_fails(monkeypatch) -> None:
    instrument = _instrument()
    session = MagicMock()
    session.get.side_effect = [MagicMock(), instrument]
    session.scalar.return_value = "accessible"
    monkeypatch.setattr(
        backtests_routes,
        "run_backtest_for_workspace",
        MagicMock(side_effect=BacktestProvisioningError("no_candles", "No candles")),
    )
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{uuid4()}/backtests",
            json={**_BODY, "instrument_id": str(instrument.id)},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_create_backtest_returns_the_persisted_run(monkeypatch) -> None:
    instrument = _instrument()
    run = _backtest_run()
    session = MagicMock()
    session.get.side_effect = [MagicMock(), instrument]
    session.scalar.return_value = "accessible"
    monkeypatch.setattr(
        backtests_routes, "run_backtest_for_workspace", MagicMock(return_value=[run])
    )
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{uuid4()}/backtests",
            json={**_BODY, "instrument_id": str(instrument.id)},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["status"] == "succeeded"


def test_create_backtest_rejects_a_non_positive_time_range() -> None:
    session = MagicMock()
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{uuid4()}/backtests",
            json={**_BODY, "from_time": "2026-09-02T00:00:00Z", "to_time": "2026-09-01T00:00:00Z"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_list_backtests() -> None:
    workspace_id = uuid4()
    run = _backtest_run(workspace_id=workspace_id)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [run]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/backtests")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["id"] == str(run.id)


def test_get_backtest_404_for_a_run_in_another_workspace() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{uuid4()}/backtests/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_list_backtest_trades() -> None:
    workspace_id = uuid4()
    run = _backtest_run(workspace_id=workspace_id)
    trade = BacktestTrade(
        id=uuid4(),
        backtest_run_id=run.id,
        sequence_no=1,
        instrument_id=uuid4(),
        side="buy",
        entry_time=datetime.now(UTC),
        exit_time=None,
        entry_price=Decimal("100"),
        exit_price=None,
        quantity=Decimal("1"),
        fees=Decimal("0"),
        realized_pnl=None,
    )
    session = MagicMock()
    session.scalar.return_value = run  # _get_backtest_run
    session.scalars.return_value.all.return_value = [trade]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/backtests/{run.id}/trades")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["side"] == "buy"


def test_create_backtest_requires_operator_role() -> None:
    session = MagicMock()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    # require_operator_role intentionally left un-overridden: with no session
    # cookie, it falls through to require_authenticated_user's real 401.
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/backtests", json=_BODY)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
