"""Unit 8 (docs/plans/horizon5-implementation-plan.md): Outbox claiming and
notification delivery.

`deliver_pending_notifications`'s control flow (recipient resolution, adapter
success/failure handling, the SystemEvent-correlation lookup) is exercised
against a MagicMock Session, matching this codebase's convention for
DB-touching application functions. `claim_pending_outbox_events`'s actual
query behavior -- which rows `pending`/`FOR UPDATE SKIP LOCKED` include or
exclude -- can only be verified meaningfully against a real database, so
those tests are opt-in (require WORKER_TEST_DATABASE_URL), matching
tests/test_worker_leases_postgres.py's convention.
"""

import os
import smtplib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import OutboxEvent, SystemEvent
from app.models.notifications import Notification
from app.notifications.application import deliver_notifications as deliver
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
_requires_postgres = pytest.mark.skipif(
    not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL"
)
ROOT = Path(__file__).resolve().parents[1]


def _migrate(engine) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@pytest.fixture(scope="module")
def pg_sessionmaker():
    """Module-scoped: migrates a fresh `worker_test_*` database to head once,
    truncates the two tables these tests touch between tests (see
    `pg_session` below) rather than re-migrating per test."""
    if not TEST_URL:
        pytest.skip("Requires dedicated WORKER_TEST_DATABASE_URL")
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
    _migrate(engine)
    yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS fx CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


@pytest.fixture
def pg_session(pg_sessionmaker):
    with pg_sessionmaker() as db:
        yield db
        db.rollback()
        db.execute(text("TRUNCATE fx.outbox_event, fx.system_event, fx.notification CASCADE"))
        db.commit()


def _outbox_event(**overrides: object) -> OutboxEvent:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        aggregate_type="trading_bot",
        aggregate_id=uuid4(),
        event_type="trading_halt.activated",
        payload={"level": "entry_halted"},
        correlation_id=uuid4(),
        status="pending",
        attempts=0,
    )
    defaults.update(overrides)
    return OutboxEvent(**defaults)


def _system_event(**overrides: object) -> SystemEvent:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        severity="warning",
        category="risk",
        event_type="trading_halt.activated",
        source_type="risk_gate",
        correlation_id=uuid4(),
        message="entry halted",
        payload={},
    )
    defaults.update(overrides)
    return SystemEvent(**defaults)


# ---- deliver_pending_notifications (MagicMock Session) ----


def test_delivers_to_each_resolved_recipient_and_marks_the_event_published() -> None:
    event = _outbox_event()
    system_event = _system_event(workspace_id=uuid4())
    db = MagicMock()
    db.scalars.return_value.all.return_value = [event]
    db.scalar.return_value = system_event
    adapter = MagicMock()

    def resolver(_event: OutboxEvent, _system_event: SystemEvent) -> list[tuple[str, str]]:
        return [("email", "a@example.com"), ("email", "b@example.com")]

    processed = deliver.deliver_pending_notifications(
        db, adapter=adapter, recipient_resolver=resolver
    )

    assert processed == 1
    assert event.status == "published"
    assert adapter.send.call_count == 2
    added = [call.args[0] for call in db.add.call_args_list]
    notifications = [obj for obj in added if isinstance(obj, Notification)]
    assert len(notifications) == 2
    assert all(n.status == "sent" for n in notifications)
    assert all(n.workspace_id == system_event.workspace_id for n in notifications)
    assert all(n.event_id == system_event.id for n in notifications)
    db.commit.assert_called_once()


@pytest.mark.parametrize("exception", [OSError("connection refused"), smtplib.SMTPException("bad")])
def test_marks_the_notification_failed_when_the_adapter_raises(exception: Exception) -> None:
    """Both exception families must be caught: OSError alone only covers
    connection-level failures, not smtplib.SMTPException's subclasses (auth
    failure, recipient refused), which inherit from Exception directly."""
    event = _outbox_event()
    system_event = _system_event()
    db = MagicMock()
    db.scalars.return_value.all.return_value = [event]
    db.scalar.return_value = system_event
    adapter = MagicMock()
    adapter.send.side_effect = exception

    def resolver(_event: OutboxEvent, _system_event: SystemEvent) -> list[tuple[str, str]]:
        return [("email", "a@example.com")]

    deliver.deliver_pending_notifications(db, adapter=adapter, recipient_resolver=resolver)

    added = [call.args[0] for call in db.add.call_args_list]
    notification = next(obj for obj in added if isinstance(obj, Notification))
    assert notification.status == "failed"
    assert notification.delivery_attempts == 1
    assert event.status == "published"  # the event itself was still processed


def test_marks_the_event_failed_when_no_matching_system_event_exists() -> None:
    """No SystemEvent shares this event's correlation_id -- there is nothing
    to derive workspace_id/event_id from, so nothing can be delivered."""
    event = _outbox_event()
    db = MagicMock()
    db.scalars.return_value.all.return_value = [event]
    db.scalar.return_value = None
    adapter = MagicMock()
    resolver = MagicMock()

    deliver.deliver_pending_notifications(db, adapter=adapter, recipient_resolver=resolver)

    assert event.status == "failed"
    assert event.attempts == 1
    adapter.send.assert_not_called()
    resolver.assert_not_called()


def test_processed_count_is_events_not_notifications() -> None:
    delivered_event = _outbox_event()
    empty_event = _outbox_event()
    system_event = _system_event()
    db = MagicMock()
    db.scalars.return_value.all.return_value = [delivered_event, empty_event]
    db.scalar.return_value = system_event
    adapter = MagicMock()

    def resolver(event: OutboxEvent, _system_event: SystemEvent) -> list[tuple[str, str]]:
        return [("email", "a@example.com")] if event is delivered_event else []

    processed = deliver.deliver_pending_notifications(
        db, adapter=adapter, recipient_resolver=resolver
    )

    assert processed == 2  # both events processed
    assert adapter.send.call_count == 1  # only one recipient resolved


# ---- claim_pending_outbox_events (real PostgreSQL) ----


@_requires_postgres
def test_claim_pending_outbox_events_excludes_non_pending_rows(pg_session) -> None:
    pending = _outbox_event(status="pending")
    published = _outbox_event(status="published")
    failed = _outbox_event(status="failed")
    pg_session.add_all([pending, published, failed])
    pg_session.commit()

    claimed = deliver.claim_pending_outbox_events(pg_session)
    pg_session.commit()

    assert {event.id for event in claimed} == {pending.id}


@_requires_postgres
def test_claim_pending_outbox_events_is_safe_for_concurrent_workers(pg_sessionmaker) -> None:
    """`FOR UPDATE SKIP LOCKED`: two workers racing to claim the same pending
    row must not both get it -- the second one's still-open transaction skips
    the row the first is holding, instead of blocking on it or double-claiming
    it once the first commits."""
    with pg_sessionmaker() as setup:
        event = _outbox_event(status="pending")
        setup.add(event)
        setup.commit()
        event_id = event.id

    barrier = Barrier(2)
    results: list[list] = []

    def claim() -> list:
        barrier.wait(timeout=5)
        with pg_sessionmaker() as db:
            claimed = deliver.claim_pending_outbox_events(db)
            barrier2.wait(timeout=5)  # hold the row locked until both have queried
            db.commit()
            return claimed

    barrier2 = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))

    claimed_ids = [event.id for result in results for event in result]
    assert claimed_ids == [event_id]  # exactly one of the two workers got it
