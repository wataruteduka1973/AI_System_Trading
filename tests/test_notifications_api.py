"""The in-app notifications API (app/api/routes/notifications.py): the HTTP layer against a
MagicMock session; the queries themselves are checked on a real PostgreSQL in
tests/test_notifications_api_postgres.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.audit import SystemEvent
from app.models.notifications import Notification
from app.models.workspace import AppUser
from app.security.rbac import require_viewer_role
from fastapi.testclient import TestClient

client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")


def _override(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _USER


def _row(status: str = "sent", acknowledged_at: datetime | None = None):
    event = SystemEvent(
        id=uuid4(),
        workspace_id=uuid4(),
        severity="critical",
        category="risk",
        event_type="user_emergency_stop",
        message="緊急停止が実行されました(Bot単位)",
        payload={"scope_type": "bot"},
        occurred_at=datetime(2026, 10, 7, 1, 0, tzinfo=UTC),
    )
    notification = Notification(
        id=uuid4(),
        workspace_id=event.workspace_id,
        event_id=event.id,
        channel="in_app",
        recipient_ref=str(_USER.id),
        status=status,
        acknowledged_at=acknowledged_at,
    )
    return notification, event


def test_lists_the_event_behind_each_notification_with_the_unread_count() -> None:
    notification, event = _row()
    session = MagicMock()
    session.execute.return_value.all.return_value = [(notification, event)]
    session.scalar.return_value = 3
    _override(session)
    try:
        response = client.get(f"/api/v1/workspaces/{event.workspace_id}/notifications")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["unacknowledged_count"] == 3
    (item,) = body["items"]
    assert item["id"] == str(notification.id)
    assert (item["severity"], item["category"]) == ("critical", "risk")
    assert item["message"] == "緊急停止が実行されました(Bot単位)"
    assert item["payload"] == {"scope_type": "bot"}
    assert item["status"] == "sent" and item["acknowledged_at"] is None


def test_the_list_is_limited_to_the_signed_in_persons_own_in_app_notifications() -> None:
    session = MagicMock()
    session.execute.return_value.all.return_value = []
    session.scalar.return_value = 0
    _override(session)
    try:
        client.get(
            f"/api/v1/workspaces/{uuid4()}/notifications", params={"unacknowledged_only": True}
        )
    finally:
        app.dependency_overrides.clear()

    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": False}))
    params = statement.compile().params
    assert "notification.channel" in sql and "notification.recipient_ref" in sql
    assert str(_USER.id) in params.values()  # someone else's are never listed
    assert "sent" in params.values()  # unacknowledged only


def test_the_limit_is_bounded() -> None:
    _override(MagicMock())
    try:
        too_many = client.get(f"/api/v1/workspaces/{uuid4()}/notifications", params={"limit": 201})
        none = client.get(f"/api/v1/workspaces/{uuid4()}/notifications", params={"limit": 0})
    finally:
        app.dependency_overrides.clear()

    assert too_many.status_code == 422 and none.status_code == 422


def test_acknowledging_records_when_and_by_whom() -> None:
    notification, event = _row()
    session = MagicMock()
    session.execute.return_value.one_or_none.return_value = (notification, event)
    _override(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{event.workspace_id}/notifications/{notification.id}/acknowledge"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
    assert notification.acknowledged_by == _USER.id
    assert notification.acknowledged_at is not None
    session.commit.assert_called_once()


def test_acknowledging_again_changes_nothing() -> None:
    first = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
    notification, event = _row(status="acknowledged", acknowledged_at=first)
    session = MagicMock()
    session.execute.return_value.one_or_none.return_value = (notification, event)
    _override(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{event.workspace_id}/notifications/{notification.id}/acknowledge"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert notification.acknowledged_at == first
    session.commit.assert_not_called()


def test_someone_elses_or_unknown_notification_is_404() -> None:
    session = MagicMock()
    session.execute.return_value.one_or_none.return_value = None
    _override(session)
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/notifications/{uuid4()}/acknowledge")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    session.commit.assert_not_called()


def test_acknowledge_all_reports_how_many() -> None:
    session = MagicMock()
    session.execute.return_value.all.return_value = [(uuid4(),), (uuid4(),)]
    _override(session)
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/notifications/acknowledge-all")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"acknowledged": 2}
    session.commit.assert_called_once()


def test_the_endpoints_need_a_session() -> None:
    session = MagicMock()
    app.dependency_overrides[get_db] = lambda: session  # the viewer dependency is not overridden
    try:
        listed = client.get(f"/api/v1/workspaces/{uuid4()}/notifications")
        acknowledged = client.post(f"/api/v1/workspaces/{uuid4()}/notifications/acknowledge-all")
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == 401 and acknowledged.status_code == 401
