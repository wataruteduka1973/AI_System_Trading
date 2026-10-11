"""The missing-candle API (app/api/routes/market_data_gaps.py): the HTTP layer against stubbed
queries; the queries themselves are checked on a real PostgreSQL in
tests/test_data_gaps_postgres.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.db.session import get_db
from app.main import app
from app.market_data.application import gap_reports
from app.market_data.application import use_cases as market_data_application
from app.market_data.application.use_cases import MarketDataApplicationError
from app.models.market_data import BackfillJob, MarketDataGap
from app.models.workspace import AppUser
from app.security.rbac import require_operator_role, require_viewer_role
from fastapi.testclient import TestClient

client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")
WORKSPACE = uuid4()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    session = MagicMock()
    session.get.return_value = object()  # the workspace exists
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    app.dependency_overrides[require_operator_role] = lambda: _USER
    yield
    app.dependency_overrides.clear()


def _gap(**overrides):
    values = dict(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1h",
        from_time=datetime(2026, 9, 7, 14, tzinfo=UTC),
        to_time=datetime(2026, 9, 9, 6, tzinfo=UTC),
        expected_count=40,
        missing_count=40,
        reason_code="internal_missing_candles",
        status="open",
        detected_at=datetime(2026, 10, 3, 9, tzinfo=UTC),
        resolved_at=None,
    )
    values.update(overrides)
    return MarketDataGap(**values)


def _job(gap, **overrides):
    values = dict(
        id=uuid4(),
        gap_id=gap.id,
        workspace_id=WORKSPACE,
        instrument_id=gap.instrument_id,
        timeframe=gap.timeframe,
        from_time=gap.from_time,
        to_time=gap.to_time,
        trigger_type="manual",
        status="failed",
        attempts=3,
        rows_written=0,
        validation_result={},
        error_code="configuration_error",
        started_at=None,
        finished_at=None,
        created_at=datetime(2026, 10, 8, 1, tzinfo=UTC),
        next_run_at=datetime(2026, 10, 8, 1, tzinfo=UTC),
        consecutive_failures=3,
    )
    values.update(overrides)
    return BackfillJob(**values)


def test_lists_gaps_with_their_job_and_bots_and_whether_they_can_be_filled(monkeypatch) -> None:
    gap = _gap()
    job = _job(gap)
    row = gap_reports.GapRow(
        gap=gap, symbol="BTCJPY", exchange_code="binance", latest_job=job, affected_bots=["btc-4h"]
    )
    seen = {}

    def fake(db, workspace_id, **kwargs):
        seen.update(kwargs)
        return 1, [row]

    monkeypatch.setattr(gap_reports, "list_gaps", fake)

    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/market-data-gaps",
        params={"status": ["open", "ignored"], "timeframe": "1h", "limit": 10, "offset": 20},
    )

    assert response.status_code == 200
    body = response.json()
    (item,) = body["items"]
    assert body["total"] == 1
    assert (item["symbol"], item["timeframe"], item["status"]) == ("BTCJPY", "1h", "open")
    assert item["missing_count"] == 40 and item["can_backfill"] is True
    assert item["affected_bots"] == ["btc-4h"]
    assert item["latest_job"]["status"] == "failed"
    assert item["latest_job"]["error_code"] == "configuration_error"
    assert seen["statuses"] == ["open", "ignored"]
    assert (seen["timeframe"], seen["limit"], seen["offset"]) == ("1h", 10, 20)


def test_a_gap_with_a_job_in_flight_or_of_a_public_history_cannot_be_filled() -> None:
    gap = _gap()

    def can(exchange, job=None, status="open"):
        gap.status = status
        return gap_reports.GapRow(gap, "X", exchange, job).can_backfill

    assert can("binance") is True
    assert can("binance", _job(gap, status="queued")) is False
    assert can("binance", _job(gap, status="running")) is False
    assert can("binance", _job(gap, status="failed")) is True  # a failed one may be tried again
    assert can("binance_public") is False
    assert can("binance", status="resolved") is False
    assert can("binance", status="ignored") is False


def test_the_summary_is_a_list_per_series(monkeypatch) -> None:
    instrument = uuid4()
    monkeypatch.setattr(
        gap_reports,
        "summarize_gaps",
        lambda db, workspace_id: [
            gap_reports.GapSummaryRow(instrument, "BTCJPY", "binance", "1h", 1, 40, 0, 2)
        ],
    )

    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/market-data-gaps/summary")

    assert response.json() == [
        {
            "instrument_id": str(instrument),
            "symbol": "BTCJPY",
            "exchange_code": "binance",
            "timeframe": "1h",
            "open_count": 1,
            "open_missing": 40,
            "ignored_count": 0,
            "resolved_count": 2,
        }
    ]


def test_filling_a_gap_answers_accepted_with_the_queued_job(monkeypatch) -> None:
    gap = _gap()
    job = _job(gap, status="queued", error_code=None, attempts=0, consecutive_failures=0)
    monkeypatch.setattr(
        market_data_application,
        "enqueue_gap_backfill",
        lambda db, workspace_id, gap_id, validator: job,
    )

    response = client.post(f"/api/v1/workspaces/{WORKSPACE}/market-data-gaps/{gap.id}/backfill")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["from_time"].startswith("2026-09-07")


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("gap_not_found", 404),
        ("gap_not_open", 409),
        ("research_instrument", 409),
        ("overlapping_backfill", 409),
        ("instrument_unavailable", 409),
        ("credentials_missing", 409),
        ("invalid_input", 422),
    ],
)
def test_refusals_map_to_http_statuses(monkeypatch, code, status_code) -> None:
    def refuse(db, workspace_id, gap_id, validator):
        raise MarketDataApplicationError(code, "理由")

    monkeypatch.setattr(market_data_application, "enqueue_gap_backfill", refuse)

    response = client.post(f"/api/v1/workspaces/{WORKSPACE}/market-data-gaps/{uuid4()}/backfill")

    assert response.status_code == status_code
    assert response.json()["detail"] == "理由"


def test_bad_query_values_are_rejected() -> None:
    base = f"/api/v1/workspaces/{WORKSPACE}/market-data-gaps"

    assert client.get(base, params={"timeframe": "2h"}).status_code == 422
    assert client.get(base, params={"limit": 0}).status_code == 422
    assert client.get(base, params={"offset": -1}).status_code == 422
