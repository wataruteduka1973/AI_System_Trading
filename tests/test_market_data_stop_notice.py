"""`announce_stopped` and the market-data worker watchdog
(docs/plans/notification-sources.md). The transitions that call them (a subscription becoming
blocked, a backfill failing) are checked against a real PostgreSQL in
tests/test_worker_leases_postgres.py."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from app.market_data.application import worker_watchdog
from app.market_data.infrastructure.models import WorkerBackfill, WorkerSubscription
from app.market_data.infrastructure.stop_notice import announce_stopped
from app.models.audit import OutboxEvent, SystemEvent
from app.models.instruments import Instrument

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _events(db):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], SystemEvent)]


def _target(model, **overrides):
    defaults = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        instrument_id=uuid4(),
        timeframe="4h",
        consecutive_failures=3,
    )
    defaults.update(overrides)
    return model(**defaults)


def _db(already_told: int = 0, symbol: str = "BTCUSDT") -> MagicMock:
    db = MagicMock()
    db.get.return_value = Instrument(id=uuid4(), symbol=symbol)
    db.scalar.return_value = already_told
    return db


def test_a_blocked_subscription_is_an_error_naming_the_instrument_and_the_cause() -> None:
    db = _db()
    target = _target(WorkerSubscription)

    announce_stopped(db, target, error_code="authentication_failed")

    (event,) = _events(db)
    assert (event.severity, event.category, event.event_type) == (
        "error",
        "market_data",
        "market_data_stopped",
    )
    assert (
        event.message == "BTCUSDT 4h のローソク足の自動取得が停止しました(原因: 取引所の認証に失敗)"
    )
    assert event.workspace_id == target.workspace_id
    assert (event.target_type, event.target_id) == ("instrument", target.instrument_id)
    assert event.payload == {
        "kind": "subscription",
        "symbol": "BTCUSDT",
        "instrument_id": str(target.instrument_id),
        "timeframe": "4h",
        "error_code": "authentication_failed",
        "consecutive_failures": 3,
    }
    (outbox,) = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], OutboxEvent)]
    assert (outbox.aggregate_type, outbox.aggregate_id) == ("market_data_subscription", target.id)
    db.commit.assert_not_called()  # the caller's transaction owns it


def test_a_failed_backfill_is_a_warning() -> None:
    db = _db()

    announce_stopped(db, _target(WorkerBackfill), error_code="rate_limited")

    (event,) = _events(db)
    assert event.severity == "warning"
    assert "過去データの取得が停止しました(原因: 取引所のレート制限)" in event.message
    assert event.payload["kind"] == "backfill"


def test_an_unknown_cause_shows_its_code() -> None:
    db = _db()

    announce_stopped(db, _target(WorkerSubscription), error_code="internal_error")

    assert "(原因: internal_error)" in _events(db)[0].message


def test_the_other_time_frames_failing_for_the_same_cause_are_not_announced_again() -> None:
    db = _db(already_told=1)

    announce_stopped(db, _target(WorkerSubscription), error_code="authentication_failed")

    db.add.assert_not_called()
    query = str(db.scalar.call_args.args[0])
    assert "system_event.target_id" in query and "system_event.reason_code" in query


# ---- the market-data worker watchdog ----

OVERDUE = timedelta(seconds=600)


def _wd_db(rows, last_alert=None):
    db = MagicMock()
    db.execute.return_value.all.return_value = rows
    db.scalar.return_value = last_alert
    return db


def test_an_overdue_subscription_raises_one_alert_per_workspace() -> None:
    workspace = uuid4()
    due = NOW - timedelta(minutes=45)
    db = _wd_db([(workspace, "BTCUSDT", "4h", due), (workspace, "ETHUSDT", "1h", due)])

    alerted = worker_watchdog.check_market_data_worker(db, now=NOW, overdue_after=OVERDUE)

    assert alerted == 1
    (event,) = _events(db)
    assert (event.severity, event.category, event.event_type) == (
        "error",
        "market_data",
        "market_data_worker_stalled",
    )
    assert "45分遅れ" in event.message and "BTCUSDT 4h, ETHUSDT 1h" in event.message
    assert event.payload["overdue"] == ["BTCUSDT 4h", "ETHUSDT 1h"]
    db.commit.assert_called_once_with()


def test_nothing_overdue_means_no_alert() -> None:
    db = _wd_db([])

    assert worker_watchdog.check_market_data_worker(db, now=NOW, overdue_after=OVERDUE) == 0

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_the_same_outage_is_reported_once_and_a_new_one_again() -> None:
    workspace = uuid4()
    due = NOW - timedelta(minutes=45)
    same_outage = _wd_db(
        [(workspace, "BTCUSDT", "4h", due)], last_alert=NOW - timedelta(minutes=10)
    )
    assert (
        worker_watchdog.check_market_data_worker(same_outage, now=NOW, overdue_after=OVERDUE) == 0
    )

    served_then_late = _wd_db(
        [(workspace, "BTCUSDT", "4h", NOW - timedelta(minutes=20))],
        last_alert=NOW - timedelta(minutes=40),
    )
    assert (
        worker_watchdog.check_market_data_worker(served_then_late, now=NOW, overdue_after=OVERDUE)
        == 1
    )


def test_only_enabled_unblocked_subscriptions_count() -> None:
    db = _wd_db([])

    worker_watchdog.check_market_data_worker(db, now=NOW, overdue_after=OVERDUE)

    sql = str(db.execute.call_args.args[0])
    assert "market_data_subscription.enabled IS true" in sql.replace("= true", "IS true")
    assert "blocked_reason IS NULL" in sql  # a blocked one was announced when it blocked
