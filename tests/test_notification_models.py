"""Unit 8 (docs/plans/horizon5-implementation-plan.md): `Notification`/
`OutboxEvent` ORM persistence and the DB CHECK constraints they must respect
-- opt-in, requires a dedicated WORKER_TEST_DATABASE_URL (same convention as
tests/test_worker_leases_postgres.py).
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import OutboxEvent, SystemEvent
from app.models.notifications import Notification
from app.models.workspace import Workspace
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


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
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS fx CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


@pytest.fixture
def db(sessions):
    with sessions() as session:
        yield session
        session.rollback()
        session.execute(
            text("TRUNCATE fx.notification, fx.outbox_event, fx.system_event, fx.workspace CASCADE")
        )
        session.commit()


def _workspace() -> Workspace:
    return Workspace(id=uuid4(), name="Test", status="active")


def _system_event(workspace_id) -> SystemEvent:
    return SystemEvent(
        id=uuid4(),
        workspace_id=workspace_id,
        severity="warning",
        category="risk",
        event_type="trading_halt.activated",
        source_type="risk_gate",
        correlation_id=uuid4(),
        message="entry halted",
        payload={},
    )


# ---- OutboxEvent ----


def test_outbox_event_round_trips(db) -> None:
    event = OutboxEvent(
        id=uuid4(),
        aggregate_type="trading_bot",
        aggregate_id=uuid4(),
        event_type="trading_halt.activated",
        payload={"level": "entry_halted"},
        correlation_id=uuid4(),
    )
    db.add(event)
    db.commit()

    fetched = db.get(OutboxEvent, event.id)
    assert fetched.status == "pending"  # server_default
    assert fetched.attempts == 0  # server_default
    assert fetched.payload == {"level": "entry_halted"}


def test_outbox_event_rejects_an_invalid_status(db) -> None:
    event = OutboxEvent(
        id=uuid4(),
        aggregate_type="trading_bot",
        aggregate_id=uuid4(),
        event_type="x",
        payload={},
        correlation_id=uuid4(),
        status="not-a-real-status",
    )
    db.add(event)
    with pytest.raises(IntegrityError):
        db.commit()


# ---- Notification ----


def test_notification_round_trips(db) -> None:
    workspace = _workspace()
    db.add(workspace)
    db.flush()
    system_event = _system_event(workspace.id)
    db.add(system_event)
    db.flush()

    notification = Notification(
        id=uuid4(),
        workspace_id=workspace.id,
        event_id=system_event.id,
        channel="email",
        recipient_ref="owner@example.com",
        delivery_attempts=0,
    )
    db.add(notification)
    db.commit()

    fetched = db.get(Notification, notification.id)
    assert fetched.status == "queued"  # server_default
    assert fetched.channel == "email"


def test_notification_rejects_an_invalid_channel(db) -> None:
    workspace = _workspace()
    db.add(workspace)
    db.flush()
    system_event = _system_event(workspace.id)
    db.add(system_event)
    db.flush()

    notification = Notification(
        id=uuid4(),
        workspace_id=workspace.id,
        event_id=system_event.id,
        channel="carrier-pigeon",
        recipient_ref="owner@example.com",
        delivery_attempts=0,
    )
    db.add(notification)
    with pytest.raises(IntegrityError):
        db.commit()


def test_notification_rejects_negative_delivery_attempts(db) -> None:
    workspace = _workspace()
    db.add(workspace)
    db.flush()
    system_event = _system_event(workspace.id)
    db.add(system_event)
    db.flush()

    notification = Notification(
        id=uuid4(),
        workspace_id=workspace.id,
        event_id=system_event.id,
        channel="email",
        recipient_ref="owner@example.com",
        delivery_attempts=-1,
    )
    db.add(notification)
    with pytest.raises(IntegrityError):
        db.commit()


def test_notification_requires_an_existing_system_event(db) -> None:
    workspace = _workspace()
    db.add(workspace)
    db.flush()

    notification = Notification(
        id=uuid4(),
        workspace_id=workspace.id,
        event_id=uuid4(),  # no such system_event row
        channel="email",
        recipient_ref="owner@example.com",
        delivery_attempts=0,
    )
    db.add(notification)
    with pytest.raises(IntegrityError):
        db.commit()
