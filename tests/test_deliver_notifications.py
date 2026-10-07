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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import OutboxEvent, SystemEvent
from app.models.notifications import Notification
from app.models.strategy import TradingHalt
from app.models.workspace import AppUser, UserMembership, Workspace
from app.notifications.application import deliver_notifications as deliver
from app.notifications.application.recipients import NOTIFIED_ROLES, workspace_member_recipients
from app.trading.application.trading_halt import HaltScope, activate_or_escalate
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
_requires_postgres = pytest.mark.skipif(
    not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL"
)
# The whole module runs in CI's PostgreSQL job (selected by marker, see .github/workflows/ci.yml).
pytestmark = pytest.mark.postgres
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


def _db(event: OutboxEvent | list[OutboxEvent], system_event: SystemEvent | None, existing=None):
    """`db.scalars` claims the events; `db.scalar` answers the SystemEvent lookup first and
    then, per recipient, the lookup of an earlier `Notification` for the same
    (event, channel, recipient)."""
    db = MagicMock()
    db.scalars.return_value.all.return_value = event if isinstance(event, list) else [event]
    db.scalar.side_effect = lambda statement: (
        system_event if "system_event" in str(statement) else existing
    )
    return db


def _recipients(*recipients: tuple[str, str]):
    return lambda _db, _event, _system_event: list(recipients)


def _notifications(db: MagicMock) -> list[Notification]:
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], Notification)]


def test_delivers_to_each_resolved_recipient_and_marks_the_event_published() -> None:
    event = _outbox_event()
    system_event = _system_event(workspace_id=uuid4())
    db = _db(event, system_event)
    email, in_app = MagicMock(), MagicMock()

    processed = deliver.deliver_pending_notifications(
        db,
        adapters={"email": email, "in_app": in_app},
        recipient_resolver=_recipients(("email", "a@example.com"), ("in_app", "user-1")),
    )

    assert processed == 1
    assert event.status == "published"
    assert event.published_at is not None
    email.send.assert_called_once()
    in_app.send.assert_called_once()
    notifications = _notifications(db)
    assert [(n.channel, n.recipient_ref) for n in notifications] == [
        ("email", "a@example.com"),
        ("in_app", "user-1"),
    ]
    assert all(n.status == "sent" and n.sent_at is not None for n in notifications)
    assert all(n.workspace_id == system_event.workspace_id for n in notifications)
    assert all(n.event_id == system_event.id for n in notifications)
    db.commit.assert_called_once()


def test_the_message_names_the_severity_and_carries_the_payload() -> None:
    system_event = _system_event(
        severity="critical", message="Emergency stop requested", payload={"scope_type": "bot"}
    )
    db = _db(_outbox_event(), system_event)
    adapter = MagicMock()

    deliver.deliver_pending_notifications(
        db, adapters={"email": adapter}, recipient_resolver=_recipients(("email", "a@example.com"))
    )

    kwargs = adapter.send.call_args.kwargs
    assert kwargs["recipient"] == "a@example.com"
    assert kwargs["subject"] == "[CRITICAL] Emergency stop requested"
    assert '"scope_type": "bot"' in kwargs["body"]


@pytest.mark.parametrize("exception", [OSError("connection refused"), smtplib.SMTPException("bad")])
def test_a_failed_delivery_is_retried_later_not_given_up_on(exception: Exception) -> None:
    """Both exception families must be caught: OSError alone only covers
    connection-level failures, not smtplib.SMTPException's subclasses (auth
    failure, recipient refused), which inherit from Exception directly."""
    event = _outbox_event()
    db = _db(event, _system_event())
    adapter = MagicMock()
    adapter.send.side_effect = exception
    before = datetime.now(UTC)

    deliver.deliver_pending_notifications(
        db, adapters={"email": adapter}, recipient_resolver=_recipients(("email", "a@example.com"))
    )

    (notification,) = _notifications(db)
    assert notification.status == "failed"
    assert notification.delivery_attempts == 1
    assert event.status == "pending"  # not published: someone has not been told
    assert event.attempts == 1
    assert event.available_at >= before + timedelta(seconds=30)  # parked for the backoff


def test_the_backoff_grows_and_the_event_fails_after_the_last_attempt() -> None:
    adapter = MagicMock()
    adapter.send.side_effect = OSError("down")
    delays = []
    for attempts in (0, 1, 2, 3):
        event = _outbox_event(attempts=attempts)
        before = datetime.now(UTC)
        deliver.deliver_pending_notifications(
            _db(event, _system_event()),
            adapters={"email": adapter},
            recipient_resolver=_recipients(("email", "a@example.com")),
        )
        assert event.status == "pending"
        delays.append(round((event.available_at - before).total_seconds() / 10) * 10)
    assert delays == [60, 120, 240, 480]

    last = _outbox_event(attempts=deliver.MAX_ATTEMPTS - 1)
    deliver.deliver_pending_notifications(
        _db(last, _system_event()),
        adapters={"email": adapter},
        recipient_resolver=_recipients(("email", "a@example.com")),
    )
    assert last.status == "failed"
    assert last.attempts == deliver.MAX_ATTEMPTS


def test_a_retry_sends_only_to_those_who_have_not_got_it_and_reuses_the_failed_row() -> None:
    event = _outbox_event(attempts=1)
    system_event = _system_event()
    failed_before = Notification(
        workspace_id=system_event.workspace_id,
        event_id=system_event.id,
        channel="email",
        recipient_ref="a@example.com",
        status="failed",
        delivery_attempts=1,
    )
    db = _db(event, system_event, existing=failed_before)
    adapter = MagicMock()

    deliver.deliver_pending_notifications(
        db, adapters={"email": adapter}, recipient_resolver=_recipients(("email", "a@example.com"))
    )

    adapter.send.assert_called_once()
    assert _notifications(db) == []  # the same row was retried, not a second one
    assert failed_before.status == "sent"
    assert event.status == "published"


@pytest.mark.parametrize("status", ["sent", "acknowledged"])
def test_someone_who_already_got_it_is_not_sent_it_again(status: str) -> None:
    event = _outbox_event(attempts=1)
    system_event = _system_event()
    delivered = Notification(
        workspace_id=system_event.workspace_id,
        event_id=system_event.id,
        channel="email",
        recipient_ref="a@example.com",
        status=status,
        delivery_attempts=0,
    )
    db = _db(event, system_event, existing=delivered)
    adapter = MagicMock()

    deliver.deliver_pending_notifications(
        db, adapters={"email": adapter}, recipient_resolver=_recipients(("email", "a@example.com"))
    )

    adapter.send.assert_not_called()
    assert event.status == "published"


def test_a_channel_without_an_adapter_is_skipped_not_failed() -> None:
    event = _outbox_event()
    db = _db(event, _system_event())
    in_app = MagicMock()

    deliver.deliver_pending_notifications(
        db,
        adapters={"in_app": in_app},  # SMTP is not configured
        recipient_resolver=_recipients(("email", "a@example.com"), ("in_app", "user-1")),
    )

    assert [n.channel for n in _notifications(db)] == ["in_app"]
    assert event.status == "published"  # nothing to retry for the email that cannot be sent


def test_marks_the_event_failed_when_no_matching_system_event_exists() -> None:
    """No SystemEvent shares this event's correlation_id -- there is nothing
    to derive workspace_id/event_id from, so nothing can be delivered."""
    event = _outbox_event()
    db = _db(event, None)
    adapter = MagicMock()
    resolver = MagicMock()

    deliver.deliver_pending_notifications(
        db, adapters={"email": adapter}, recipient_resolver=resolver
    )

    assert event.status == "failed"
    assert event.attempts == 1
    adapter.send.assert_not_called()
    resolver.assert_not_called()


def test_processed_count_is_events_not_notifications() -> None:
    delivered_event = _outbox_event()
    empty_event = _outbox_event()
    db = _db([delivered_event, empty_event], _system_event())
    adapter = MagicMock()

    def resolver(_db, event: OutboxEvent, _system_event: SystemEvent) -> list[tuple[str, str]]:
        return [("email", "a@example.com")] if event is delivered_event else []

    processed = deliver.deliver_pending_notifications(
        db, adapters={"email": adapter}, recipient_resolver=resolver
    )

    assert processed == 2  # both events processed
    assert adapter.send.call_count == 1  # only one recipient resolved


# ---- recipients ----


def test_owners_and_operators_are_notified_in_app_and_by_email() -> None:
    db = MagicMock()
    first, second = uuid4(), uuid4()
    db.execute.return_value.all.return_value = [(first, "a@example.com"), (second, "b@example.com")]

    recipients = workspace_member_recipients(db, _outbox_event(), _system_event())

    assert recipients == [
        ("in_app", str(first)),
        ("email", "a@example.com"),
        ("in_app", str(second)),
        ("email", "b@example.com"),
    ]
    query = str(db.execute.call_args.args[0])
    assert "user_membership.role IN" in query  # viewers are left out
    assert "app_user.status" in query  # so are disabled accounts


def test_the_roles_that_are_notified_are_the_ones_that_can_act() -> None:
    assert set(NOTIFIED_ROLES) == {"owner", "operator"}


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

    with pg_sessionmaker() as cleanup:  # this test does not use `pg_session`'s cleanup
        cleanup.execute(text("TRUNCATE fx.outbox_event CASCADE"))
        cleanup.commit()


@_requires_postgres
def test_a_retry_parked_by_its_backoff_is_not_claimed_until_it_is_due(pg_session) -> None:
    due = _outbox_event(available_at=datetime.now(UTC) - timedelta(seconds=1))
    parked = _outbox_event(available_at=datetime.now(UTC) + timedelta(minutes=5))
    pg_session.add_all([due, parked])
    pg_session.commit()

    claimed = deliver.claim_pending_outbox_events(pg_session)
    pg_session.commit()

    assert {event.id for event in claimed} == {due.id}


# ---- a halt, announced, delivered to the right people (real PostgreSQL) ----


def _seed_workspace_with_members(db) -> dict[str, object]:
    workspace = Workspace(name="notify test")
    db.add(workspace)
    db.flush()
    people = {}
    for name, role, status in (
        ("owner", "owner", "active"),
        ("operator", "operator", "active"),
        ("viewer", "viewer", "active"),
        ("disabled", "operator", "disabled"),
    ):
        user = AppUser(
            email=f"{name}-{uuid4().hex[:6]}@example.com", display_name=name, status=status
        )
        db.add(user)
        db.flush()
        db.add(UserMembership(workspace_id=workspace.id, user_id=user.id, role=role))
        people[name] = user
    db.commit()
    return {"workspace": workspace, **people}


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        self.sent.append((recipient, subject))


@_requires_postgres
def test_a_new_halt_reaches_the_owner_and_operator_but_not_viewers_or_disabled_users(
    pg_session,
) -> None:
    people = _seed_workspace_with_members(pg_session)
    workspace = people["workspace"]
    scope = HaltScope(workspace_id=workspace.id, scope_type="bot", scope_id=uuid4())

    # What the Risk Gate does on a data-delay breach, in the caller's transaction.
    activate_or_escalate(pg_session, scope, reason_code="data_delay", level="entry_halted")
    pg_session.commit()
    email, in_app = _Recorder(), _Recorder()

    processed = deliver.deliver_pending_notifications(
        pg_session,
        adapters={"email": email, "in_app": in_app},
        recipient_resolver=workspace_member_recipients,
    )

    assert processed == 1
    assert sorted(r for r, _ in email.sent) == sorted(
        [people["owner"].email, people["operator"].email]
    )
    assert sorted(r for r, _ in in_app.sent) == sorted(
        [str(people["owner"].id), str(people["operator"].id)]
    )
    assert email.sent[0][1] == "[WARNING] データ遅延により、Botが新規建玉の停止になりました"
    outbox = pg_session.scalars(select(OutboxEvent)).one()
    assert outbox.status == "published" and outbox.published_at is not None
    rows = pg_session.scalars(select(Notification)).all()
    assert len(rows) == 4 and all(n.status == "sent" for n in rows)
    assert {n.workspace_id for n in rows} == {workspace.id}
    halt = pg_session.scalars(select(TradingHalt)).one()
    assert halt.trigger_event_id == rows[0].event_id  # the halt points at what announced it


@_requires_postgres
def test_an_email_outage_is_retried_without_telling_the_in_app_user_twice(pg_session) -> None:
    people = _seed_workspace_with_members(pg_session)
    scope = HaltScope(workspace_id=people["workspace"].id, scope_type="bot", scope_id=uuid4())
    activate_or_escalate(pg_session, scope, reason_code="data_delay", level="entry_halted")
    pg_session.commit()

    class Down:
        def send(self, **_kwargs) -> None:
            raise OSError("smtp down")

    in_app = _Recorder()
    deliver.deliver_pending_notifications(
        pg_session,
        adapters={"email": Down(), "in_app": in_app},
        recipient_resolver=workspace_member_recipients,
    )
    outbox = pg_session.scalars(select(OutboxEvent)).one()
    assert outbox.status == "pending" and outbox.attempts == 1
    assert len(in_app.sent) == 2
    assert {n.status for n in pg_session.scalars(select(Notification)).all()} == {"sent", "failed"}

    # The backoff passes and SMTP is back.
    outbox.available_at = datetime.now(UTC) - timedelta(seconds=1)
    pg_session.commit()
    email = _Recorder()
    deliver.deliver_pending_notifications(
        pg_session,
        adapters={"email": email, "in_app": in_app},
        recipient_resolver=workspace_member_recipients,
    )

    assert len(email.sent) == 2  # now both addresses
    assert len(in_app.sent) == 2  # unchanged: no one was told twice
    assert pg_session.scalars(select(OutboxEvent)).one().status == "published"
    notifications = pg_session.scalars(select(Notification)).all()
    assert len(notifications) == 4 and all(n.status == "sent" for n in notifications)
    assert max(n.delivery_attempts for n in notifications) == 1  # the failed one counted its try


@_requires_postgres
def test_an_escalation_is_announced_again_but_a_repeat_breach_is_not(pg_session) -> None:
    people = _seed_workspace_with_members(pg_session)
    scope = HaltScope(workspace_id=people["workspace"].id, scope_type="bot", scope_id=uuid4())

    activate_or_escalate(pg_session, scope, reason_code="data_delay", level="entry_halted")
    activate_or_escalate(pg_session, scope, reason_code="data_delay", level="entry_halted")
    activate_or_escalate(pg_session, scope, reason_code="data_delay", level="all_trading_halted")
    pg_session.commit()

    events = pg_session.scalars(select(SystemEvent).order_by(SystemEvent.occurred_at)).all()
    assert [e.severity for e in events] == ["warning", "error"]
    assert pg_session.scalars(select(TradingHalt)).one().level == "all_trading_halted"
    assert len(pg_session.scalars(select(OutboxEvent)).all()) == 2
