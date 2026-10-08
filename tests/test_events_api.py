"""The event log API (app/api/routes/events.py): the HTTP layer against a MagicMock session and a
stubbed query; the queries themselves are checked on a real PostgreSQL in
tests/test_event_log_postgres.py."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.db.session import get_db
from app.main import app
from app.models.audit import SystemEvent
from app.models.workspace import AppUser
from app.monitoring import event_log
from app.security.rbac import require_viewer_role
from fastapi.testclient import TestClient

client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")
WORKSPACE = uuid4()


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    yield
    app.dependency_overrides.clear()


def _event(**overrides):
    values = dict(
        id=uuid4(),
        workspace_id=WORKSPACE,
        occurred_at=datetime(2026, 10, 8, 3, 0, tzinfo=UTC),
        severity="critical",
        category="risk",
        event_type="trading_halt.ledger_mismatch",
        reason_code="ledger_mismatch",
        source_type="reconciliation",
        source_id=None,
        target_type="account",
        target_id=uuid4(),
        correlation_id=uuid4(),
        message="注文と台帳の不整合を検知したため、口座の取引を止めました",
        payload={"finding_count": 1},
    )
    values.update(overrides)
    return SystemEvent(**values)


def test_lists_events_with_the_fields_the_screen_needs(monkeypatch) -> None:
    event = _event()
    monkeypatch.setattr(
        event_log,
        "query_events",
        lambda db, workspace_id, filters, *, limit, before=None: event_log.EventPage(
            items=[event], next_before=(event.occurred_at, event.id)
        ),
    )

    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/events")

    assert response.status_code == 200
    body = response.json()
    (item,) = body["items"]
    assert item["event_type"] == "trading_halt.ledger_mismatch"
    assert item["correlation_id"] == str(event.correlation_id)
    assert item["payload"] == {"finding_count": 1}
    assert body["next_before_id"] == str(event.id) and body["next_before"] is not None


def test_passes_every_filter_through(monkeypatch) -> None:
    seen = {}

    def fake(db, workspace_id, filters, *, limit, before=None):
        seen.update(filters=filters, limit=limit, before=before, workspace_id=workspace_id)
        return event_log.EventPage(items=[], next_before=None)

    monkeypatch.setattr(event_log, "query_events", fake)
    bot, correlation, before_id = uuid4(), uuid4(), uuid4()

    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/events",
        params={
            "severity": ["error", "critical"],
            "category": "risk",
            "event_type": "x",
            "reason_code": "ledger_mismatch",
            "bot_id": str(bot),
            "correlation_id": str(correlation),
            "from_time": "2026-10-01T00:00:00Z",
            "to_time": "2026-10-08T00:00:00Z",
            "q": "台帳",
            "limit": 20,
            "before": "2026-10-07T00:00:00Z",
            "before_id": str(before_id),
        },
    )

    assert response.status_code == 200
    filters = seen["filters"]
    assert filters.severities == ["error", "critical"]
    assert (filters.category, filters.event_type, filters.reason_code) == (
        "risk",
        "x",
        "ledger_mismatch",
    )
    assert (filters.bot_id, filters.correlation_id, filters.text) == (bot, correlation, "台帳")
    assert filters.from_time == datetime(2026, 10, 1, tzinfo=UTC)
    assert seen["limit"] == 20 and seen["workspace_id"] == WORKSPACE
    assert seen["before"] == (datetime(2026, 10, 7, tzinfo=UTC), before_id)


@pytest.mark.parametrize(
    "params",
    [
        {"before": "2026-10-07T00:00:00Z"},  # no before_id
        {"before_id": str(uuid4())},  # no before
        {"from_time": "2026-10-08T00:00:00Z", "to_time": "2026-10-07T00:00:00Z"},
        {"limit": 0},
        {"limit": 1000},
        {"bot_id": "not-a-uuid"},
        {"q": "x" * 101},
    ],
)
def test_rejects_requests_that_make_no_sense(params) -> None:
    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/events", params=params)

    assert response.status_code == 422


def test_the_facets_come_back_as_lists(monkeypatch) -> None:
    monkeypatch.setattr(
        event_log,
        "event_facets",
        lambda db, workspace_id: event_log.EventFacets(
            severities=["error"],
            categories=["risk"],
            event_types=["halt"],
            reason_codes=["data_delay"],
        ),
    )

    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/events/facets")

    assert response.json() == {
        "severities": ["error"],
        "categories": ["risk"],
        "event_types": ["halt"],
        "reason_codes": ["data_delay"],
    }
