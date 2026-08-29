from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.catalog import Candle, Workspace
from app.security.auth import require_owner
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

client = TestClient(app)


def _override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_owner] = lambda: "test-owner"


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
