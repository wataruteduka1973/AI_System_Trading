from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.api.routes import market_data as market_data_routes
from app.db.session import get_db
from app.main import app
from app.market_data.application import use_cases as market_data_application
from app.models.market_data import BackfillJob, Candle, MarketDataSubscription
from app.models.workspace import AppUser, Workspace
from app.schemas.market_data import MarketDataCollectionUpdate
from app.security.rbac import require_operator_role, require_viewer_role
from app.services.market_data import MarketDataAccessError
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

client = TestClient(app)


@pytest.mark.parametrize("enabled", [True, False])
def test_bulk_collection_commits_all_timeframes_once(monkeypatch, enabled: bool) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    db = MagicMock()
    preflight = MagicMock()
    update = MagicMock()
    monkeypatch.setattr(market_data_routes, "_validate_collection_configuration", preflight)
    monkeypatch.setattr(market_data_application, "_set_subscription", update)
    result = market_data_routes.update_all_market_data_subscriptions(
        workspace_id,
        MarketDataCollectionUpdate(instrument_id=instrument_id, enabled=enabled),
        db,
        "owner",
    )
    assert len(result) == 7
    assert [call.args[2].timeframe for call in update.call_args_list] == [
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "1d",
    ]
    assert all(call.args[2].enabled == enabled for call in update.call_args_list)
    db.commit.assert_called_once()
    assert preflight.call_count == int(enabled)


@pytest.mark.parametrize("action", ["backfill", "enable"])
def test_unreadable_credentials_reject_before_any_job_or_subscription_write(
    monkeypatch, action
) -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = instrument_id
    monkeypatch.setattr(market_data_application, "ensure_no_overlapping_backfill", MagicMock())
    monkeypatch.setattr(market_data_routes, "get_secret_store", MagicMock())
    monkeypatch.setattr(
        market_data_routes.CandleIngestionService,
        "validate_configuration",
        MagicMock(
            side_effect=MarketDataAccessError(
                "private-secret-never-return", "credentials_unreadable"
            )
        ),
    )
    _override_database(session)
    try:
        if action == "backfill":
            response = client.post(
                f"/api/v1/workspaces/{workspace_id}/candle-backfills",
                json={
                    "instrument_id": str(instrument_id),
                    "timeframe": "1m",
                    "days": 365,
                },
            )
        else:
            response = client.put(
                f"/api/v1/workspaces/{workspace_id}/market-data-subscriptions",
                json={
                    "instrument_id": str(instrument_id),
                    "enabled": True,
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "再検証" in response.json()["detail"]
    assert "private-secret" not in response.text
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_backfill_list_filters_timeframe_and_workspace() -> None:
    workspace_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalars.return_value.all.return_value = []
    _override_database(session)
    try:
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/candle-backfills", params={"timeframe": "5m"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    compiled = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert workspace_id in compiled.params.values()
    assert "5m" in compiled.params.values()


def test_backfill_read_exposes_worker_retry_status() -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    now = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)
    job = BackfillJob(
        id=uuid4(),
        workspace_id=workspace_id,
        instrument_id=instrument_id,
        timeframe="1m",
        from_time=now - timedelta(days=365),
        to_time=now,
        trigger_type="manual",
        status="queued",
        attempts=2,
        rows_written=0,
        validation_result={},
        error_code=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        next_run_at=now + timedelta(seconds=120),
        consecutive_failures=2,
    )
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalars.return_value.all.return_value = [job]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/candle-backfills")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()[0]
    assert body["next_run_at"] == "2026-09-16T03:02:00Z"
    assert body["consecutive_failures"] == 2


def test_subscription_read_exposes_blocked_reason_and_retry_status() -> None:
    workspace_id, instrument_id = uuid4(), uuid4()
    now = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)
    subscription = MarketDataSubscription(
        id=uuid4(),
        workspace_id=workspace_id,
        instrument_id=instrument_id,
        timeframe="1m",
        enabled=True,
        poll_interval_seconds=60,
        last_polled_at=now,
        last_success_at=None,
        last_error_code="communication_failed",
        created_at=now,
        updated_at=now,
        next_run_at=now + timedelta(seconds=120),
        consecutive_failures=3,
        blocked_reason="communication_failed",
    )
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalars.return_value.all.return_value = [subscription]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/market-data-subscriptions")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()[0]
    assert body["blocked_reason"] == "communication_failed"
    assert body["consecutive_failures"] == 3
    assert body["next_run_at"] == "2026-09-16T03:02:00Z"


_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def _override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    app.dependency_overrides[require_operator_role] = lambda: _TEST_USER


def _candle(instrument_id, open_time: datetime, close: str) -> Candle:
    candle = Candle(
        id=uuid4(),
        instrument_id=instrument_id,
        timeframe="1m",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(close) - Decimal("0.1"),
        high=Decimal(close) + Decimal("0.1"),
        low=Decimal(close) - Decimal("0.2"),
        close=Decimal(close),
        volume=Decimal("10"),
        trade_count=2,
        source="oanda",
        quality_status="complete",
        is_final=True,
    )
    return candle


def test_candle_cursor_returns_chronological_page_before_exclusive_boundary() -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    before = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    newest_first = [
        _candle(instrument_id, before - timedelta(minutes=1), "147.2"),
        _candle(instrument_id, before - timedelta(minutes=2), "147.1"),
    ]
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = instrument_id
    session.scalars.return_value.all.return_value = newest_first
    _override_database(session)
    try:
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candles",
            params={"timeframe": "1m", "limit": 2, "before": before.isoformat()},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [row["close"] for row in response.json()] == ["147.1", "147.2"]
    statement = session.scalars.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "candle.open_time <" in sql
    assert "ORDER BY fx.candle.open_time DESC, fx.candle.id DESC" in sql
    assert before in compiled.params.values()
    assert 2 in compiled.params.values()


def test_candle_cursor_requires_timezone_and_caps_page_size() -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = instrument_id
    _override_database(session)
    try:
        naive_response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candles",
            params={"before": "2026-08-27T03:00:00"},
        )
        oversized_response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candles",
            params={"limit": 501},
        )
    finally:
        app.dependency_overrides.clear()

    assert naive_response.status_code == 422
    assert naive_response.json()["detail"] == "The before cursor must include a timezone offset"
    assert oversized_response.status_code == 422
    session.scalars.assert_not_called()


def test_candle_page_rejects_instrument_outside_workspace_access() -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candles"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "verified connection" in response.json()["detail"]
    session.scalars.assert_not_called()


def test_overlapping_active_backfill_is_rejected_with_workspace_scope() -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    existing_job_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.side_effect = [instrument_id, existing_job_id]
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/candle-backfills",
            json={"instrument_id": str(instrument_id), "timeframe": "1m", "days": 30},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"] == "An overlapping backfill is already queued or running"
    session.rollback.assert_called_once()
    session.add.assert_not_called()
    duplicate_statement = session.scalar.call_args.args[0]
    compiled = duplicate_statement.compile(dialect=postgresql.dialect())
    assert workspace_id in compiled.params.values()
    assert instrument_id in compiled.params.values()
    assert "fx.backfill_job.from_time <" in str(compiled)
    assert "fx.backfill_job.to_time >" in str(compiled)


def test_coverage_accepts_an_explicit_timezone_aware_requested_range(monkeypatch) -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    requested_from = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    requested_to = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = instrument_id
    coverage = {
        "requested_from": requested_from.isoformat(),
        "requested_to": requested_to.isoformat(),
        "actual_from": requested_from.isoformat(),
        "actual_to": requested_to.isoformat(),
        "stored_count": 24,
        "expected_count": None,
        "missing_count": 0,
        "coverage_status": "complete",
        "source_limitation": None,
    }
    build_coverage = MagicMock(return_value=coverage)
    monkeypatch.setattr(market_data_application, "build_candle_coverage", build_coverage)
    _override_database(session)
    try:
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage",
            params={
                "timeframe": "1h",
                "requested_from": requested_from.isoformat(),
                "requested_to": requested_to.isoformat(),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["requested_from"] == "2026-08-01T09:00:00Z"
    build_coverage.assert_called_once_with(
        session, instrument_id, "1h", requested_from, requested_to
    )
    assert session.scalar.call_count == 1


def test_coverage_without_explicit_range_preserves_latest_backfill_fallback(monkeypatch) -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    requested_from = datetime(2026, 8, 1, tzinfo=UTC)
    requested_to = datetime(2026, 8, 2, tzinfo=UTC)
    latest_job = MagicMock(from_time=requested_from, to_time=requested_to)
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.side_effect = [instrument_id, latest_job]
    build_coverage = MagicMock(
        return_value={
            "requested_from": requested_from.isoformat(),
            "requested_to": requested_to.isoformat(),
            "actual_from": None,
            "actual_to": None,
            "stored_count": 0,
            "expected_count": None,
            "missing_count": None,
            "coverage_status": "empty",
            "source_limitation": None,
        }
    )
    monkeypatch.setattr(market_data_application, "build_candle_coverage", build_coverage)
    _override_database(session)
    try:
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    build_coverage.assert_called_once_with(
        session, instrument_id, "1m", requested_from, requested_to
    )


def test_coverage_range_requires_both_ordered_timezone_aware_boundaries() -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = instrument_id
    _override_database(session)
    endpoint = f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage"
    try:
        missing_to = client.get(endpoint, params={"requested_from": "2026-08-01T00:00:00Z"})
        naive = client.get(
            endpoint,
            params={
                "requested_from": "2026-08-01T00:00:00",
                "requested_to": "2026-08-02T00:00:00",
            },
        )
        reversed_range = client.get(
            endpoint,
            params={
                "requested_from": "2026-08-02T00:00:00Z",
                "requested_to": "2026-08-01T00:00:00Z",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert missing_to.status_code == 422
    assert (
        missing_to.json()["detail"] == "requested_from and requested_to must be provided together"
    )
    assert naive.status_code == 422
    assert naive.json()["detail"] == "Coverage range timestamps must include a timezone offset"
    assert reversed_range.status_code == 422
    assert reversed_range.json()["detail"] == "requested_from must be earlier than requested_to"


def test_coverage_rejects_instrument_outside_workspace_before_range_processing() -> None:
    workspace_id = uuid4()
    instrument_id = uuid4()
    session = MagicMock()
    session.get.return_value = Workspace(id=workspace_id, name="Personal", status="active")
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage",
            params={
                "requested_from": "2026-08-01T00:00:00Z",
                "requested_to": "2026-08-02T00:00:00Z",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "verified connection" in response.json()["detail"]
