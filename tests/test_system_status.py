"""The workspace status (app/monitoring/system_status.py, app/api/routes/system_status.py):
how the parts are summed up into problems, and the HTTP layer. The queries themselves run on a
real PostgreSQL in tests/test_system_status_postgres.py."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.workspace import AppUser
from app.monitoring import system_status as status
from app.security.rbac import require_viewer_role
from fastapi.testclient import TestClient

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")


def _trading(state="ok", stalled=()):
    return status.TradingWorkerStatus(state, 12, 5, list(stalled), 180)


def _market(state="ok", blocked=0, overdue=0):
    return status.MarketDataWorkerStatus(state, 7, blocked, overdue, 900 if overdue else None, 600)


def _notes(pending=0, oldest=None, failed=0):
    return status.NotificationStatus(pending, oldest, failed)


def test_a_healthy_system_has_no_problems() -> None:
    assert status._problems(_trading(), _market(), _notes(), {"running": 12}, {}) == []


def test_idle_parts_are_not_problems() -> None:
    problems = status._problems(_trading("idle"), _market("idle"), _notes(), {"stopped": 13}, {})

    assert problems == []


def test_a_stalled_trading_worker_names_the_bots_it_left() -> None:
    problems = status._problems(
        _trading("stalled", [f"bot-{i}" for i in range(8)]), _market(), _notes(), {}, {}
    )

    (problem,) = problems
    assert "トレーディングWorkerが止まっている" in problem
    assert "bot-4" in problem and "bot-5" not in problem  # at most five names


def test_failed_bots_and_blocked_collection_are_counted() -> None:
    problems = status._problems(
        _trading(), _market(blocked=2), _notes(), {"running": 10, "failed": 3}, {}
    )

    assert "評価に失敗して停止したBotが3件あります" in problems
    assert "停止している自動取得が2件あります" in problems


def test_a_stalled_market_data_worker_says_how_many_subscriptions_are_late() -> None:
    problems = status._problems(_trading(), _market("stalled", overdue=4), _notes(), {}, {})

    assert problems == ["市場データWorkerが止まっている可能性があります(取得が遅れている購読: 4件)"]


def test_active_halts_are_listed_from_the_most_severe() -> None:
    problems = status._problems(
        _trading(),
        _market(),
        _notes(),
        {},
        {"entry_halted": 2, "emergency_stopped": 1, "warning": 5},
    )

    assert problems == [
        "緊急停止中の取引停止(halt)が1件あります",
        "新規建玉の停止中の取引停止(halt)が2件あります",
    ]  # a `warning` halt blocks nothing and is not a problem


def test_a_notification_waiting_a_little_is_normal_but_not_for_long() -> None:
    fresh = status._problems(_trading(), _market(), _notes(pending=1, oldest=60), {}, {})
    late = status._problems(_trading(), _market(), _notes(pending=3, oldest=400), {}, {})

    assert fresh == []
    assert late == [
        "通知が3件、5分以上配信されていません(通知Workerが止まっているか、メールの配信に失敗しています)"
    ]


def test_notifications_that_could_not_be_delivered_are_reported() -> None:
    problems = status._problems(_trading(), _market(), _notes(failed=2), {}, {})

    assert problems == ["届けられなかった通知が、直近7日に2件あります"]


def test_the_overall_state_follows_the_problems(monkeypatch) -> None:
    monkeypatch.setattr(status, "_trading_worker", lambda *a: _trading("stalled", ["a"]))
    monkeypatch.setattr(status, "_market_data_worker", lambda *a: _market())
    monkeypatch.setattr(status, "_notifications", lambda *a: _notes())
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    db.scalars.return_value.all.return_value = []

    result = status.build_system_status(
        db,
        uuid4(),
        now=NOW,
        trading_stale_after=timedelta(seconds=180),
        market_data_overdue_after=timedelta(seconds=600),
    )

    assert result.overall == "attention" and len(result.problems) == 1
    assert (result.bots, result.halts, result.connections) == ({}, {}, [])

    monkeypatch.setattr(status, "_trading_worker", lambda *a: _trading())
    healthy = status.build_system_status(
        db,
        uuid4(),
        now=NOW,
        trading_stale_after=timedelta(seconds=180),
        market_data_overdue_after=timedelta(seconds=600),
    )
    assert healthy.overall == "ok" and healthy.problems == []


def test_the_endpoint_returns_the_status_with_the_configured_thresholds(monkeypatch) -> None:
    from app.api.routes import system_status as route

    seen = {}

    def fake_build(db, workspace_id, *, now, trading_stale_after, market_data_overdue_after):
        seen.update(stale=trading_stale_after, overdue=market_data_overdue_after, ws=workspace_id)
        return status.SystemStatus(
            checked_at=NOW,
            overall="attention",
            problems=["x"],
            trading_worker=_trading("stalled", ["btc"]),
            market_data_worker=_market(),
            notifications=_notes(),
            halts={"entry_halted": 1},
            bots={"running": 12},
            connections=[
                status.ConnectionStatus(
                    id=uuid4(),
                    label="Binance Spot Testnet",
                    environment="testnet",
                    status="verified",
                    verification_outcome="success",
                    last_verified_at=None,
                )
            ],
        )

    monkeypatch.setattr(route, "build_system_status", fake_build)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    workspace_id = uuid4()
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/system-status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "attention" and body["problems"] == ["x"]
    assert body["trading_worker"]["stalled_bots"] == ["btc"]
    assert body["halts"] == {"entry_halted": 1} and body["bots"] == {"running": 12}
    assert body["connections"][0]["verification_outcome"] == "success"
    assert seen["ws"] == workspace_id
    assert seen["stale"].total_seconds() == route.settings.trading_worker_stale_seconds
    assert seen["overdue"].total_seconds() == route.settings.market_data_worker_overdue_seconds


def test_the_endpoint_needs_a_session() -> None:
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        response = client.get(f"/api/v1/workspaces/{uuid4()}/system-status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
