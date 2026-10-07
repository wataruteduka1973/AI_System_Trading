"""The in-app notifications API's queries on a real PostgreSQL: whose notifications are
listed, what counts as unread, and that acknowledging only touches the caller's own. Opt-in
like the other postgres tests: needs an EMPTY dedicated worker_test_* database, and leaves
it empty."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.session import get_db
from app.main import app
from app.models.audit import SystemEvent
from app.models.notifications import Notification
from app.models.workspace import AppUser, UserMembership, Workspace
from app.security.rbac import require_viewer_role
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL"),
]
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _migrate(engine, revision, downgrade=False) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture(scope="module")
def sessions():
    url = make_url(TEST_URL)
    if not (url.database or "").startswith("worker_test_"):
        pytest.fail("Refusing non-test database; name must start with worker_test_")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        if connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
            )
        ):
            pytest.fail("Refusing a non-empty test database; use a fresh worker_test_* database")
    _migrate(engine, "head")
    yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    _migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.commit()
    engine.dispose()


@pytest.fixture
def world(sessions):
    """A workspace with two members, `me` and `other`, and one in-app notification each for
    two events (the older one for `me` is already acknowledged), plus an email one for `me`
    and a notification in another workspace for `me`."""
    with sessions() as db:
        workspace, elsewhere = Workspace(name="a"), Workspace(name="b")
        db.add_all([workspace, elsewhere])
        db.flush()
        me = AppUser(email=f"me-{uuid4().hex[:6]}@example.com", display_name="me")
        other = AppUser(email=f"other-{uuid4().hex[:6]}@example.com", display_name="other")
        db.add_all([me, other])
        db.flush()
        for user in (me, other):
            db.add(UserMembership(workspace_id=workspace.id, user_id=user.id, role="operator"))

        def event(ws, minutes_ago, message):
            row = SystemEvent(
                workspace_id=ws.id,
                severity="critical",
                category="risk",
                event_type="user_emergency_stop",
                source_type="user",
                correlation_id=uuid4(),
                message=message,
                payload={},
                occurred_at=NOW - timedelta(minutes=minutes_ago),
            )
            db.add(row)
            db.flush()
            return row

        def notify(ws, row, recipient, status="sent", channel="in_app"):
            note = Notification(
                workspace_id=ws.id,
                event_id=row.id,
                channel=channel,
                recipient_ref=recipient,
                status=status,
                delivery_attempts=0,
            )
            db.add(note)
            db.flush()
            return note

        older, newer, foreign = (
            event(workspace, 30, "older"),
            event(workspace, 5, "newer"),
            event(elsewhere, 1, "elsewhere"),
        )
        notes = {
            "mine_old": notify(workspace, older, str(me.id), status="acknowledged"),
            "mine_new": notify(workspace, newer, str(me.id)),
            "theirs": notify(workspace, newer, str(other.id)),
            "mail": notify(workspace, newer, me.email, channel="email"),
            "elsewhere": notify(elsewhere, foreign, str(me.id)),
        }
        db.commit()
        data = {"workspace": workspace, "me": me, "other": other, "notes": notes}

    def request_session():
        with sessions() as session:  # closed after each request, so no lock outlives it
            yield session

    app.dependency_overrides[get_db] = request_session
    app.dependency_overrides[require_viewer_role] = lambda: me
    yield data
    app.dependency_overrides.clear()
    with sessions() as db:
        db.execute(text("TRUNCATE fx.notification, fx.system_event, fx.user_membership CASCADE"))
        db.commit()


client = TestClient(app)


def test_lists_only_my_in_app_notifications_of_this_workspace_newest_first(world) -> None:
    response = client.get(f"/api/v1/workspaces/{world['workspace'].id}/notifications")

    body = response.json()
    assert [item["message"] for item in body["items"]] == ["newer", "older"]
    assert body["unacknowledged_count"] == 1
    assert [item["status"] for item in body["items"]] == ["sent", "acknowledged"]


def test_unacknowledged_only_leaves_out_what_was_read(world) -> None:
    body = client.get(
        f"/api/v1/workspaces/{world['workspace'].id}/notifications",
        params={"unacknowledged_only": True},
    ).json()

    assert [item["message"] for item in body["items"]] == ["newer"]
    assert body["unacknowledged_count"] == 1


def test_acknowledging_marks_it_read_and_leaves_everyone_elses_alone(world, sessions) -> None:
    mine, theirs = world["notes"]["mine_new"], world["notes"]["theirs"]

    response = client.post(
        f"/api/v1/workspaces/{world['workspace'].id}/notifications/{mine.id}/acknowledge"
    )

    assert response.status_code == 200 and response.json()["status"] == "acknowledged"
    with sessions() as db:
        stored = db.get(Notification, mine.id)
        assert (stored.status, stored.acknowledged_by) == ("acknowledged", world["me"].id)
        assert stored.acknowledged_at is not None
        assert db.get(Notification, theirs.id).status == "sent"
    after = client.get(f"/api/v1/workspaces/{world['workspace'].id}/notifications").json()
    assert after["unacknowledged_count"] == 0


def test_someone_elses_email_or_other_workspace_notification_is_not_found(world) -> None:
    for key in ("theirs", "mail", "elsewhere"):
        response = client.post(
            f"/api/v1/workspaces/{world['workspace'].id}/notifications/"
            f"{world['notes'][key].id}/acknowledge"
        )
        assert response.status_code == 404, key


def test_acknowledge_all_touches_only_my_unread_ones(world, sessions) -> None:
    response = client.post(
        f"/api/v1/workspaces/{world['workspace'].id}/notifications/acknowledge-all"
    )

    assert response.json() == {"acknowledged": 1}  # the old one was already read
    with sessions() as db:
        statuses = {
            name: db.get(Notification, note.id).status for name, note in world["notes"].items()
        }
    assert statuses == {
        "mine_old": "acknowledged",
        "mine_new": "acknowledged",
        "theirs": "sent",
        "mail": "sent",
        "elsewhere": "sent",  # another workspace is not this request's business
    }
    assert client.post(
        f"/api/v1/workspaces/{world['workspace'].id}/notifications/acknowledge-all"
    ).json() == {"acknowledged": 0}
