"""The server-side check for a stopped trading worker
(app/trading/application/worker_watchdog.py, docs/plans/worker-failure-handling.md)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.audit import OutboxEvent, SystemEvent
from app.trading.application import worker_watchdog

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
STALE = timedelta(seconds=180)


def _db(stalled_rows, last_alert=None):
    """`execute` lists the stalled (workspace, bot name, last seen) rows; `scalar` answers
    the per-workspace lookup of the last alert."""
    db = MagicMock()
    db.execute.return_value.all.return_value = stalled_rows
    db.scalar.return_value = last_alert
    return db


def _events(db):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], SystemEvent)]


def test_a_stalled_bot_raises_one_alert_for_its_workspace() -> None:
    workspace = uuid4()
    seen = NOW - timedelta(minutes=12)
    db = _db([(workspace, "btc-4h", seen), (workspace, "eth-4h", seen + timedelta(minutes=1))])

    alerted = worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE)

    assert alerted == 1
    (event,) = _events(db)
    assert (event.severity, event.category, event.event_type) == (
        "error",
        "system",
        "trading_worker_stalled",
    )
    assert event.workspace_id == workspace
    assert "12分" in event.message and "btc-4h, eth-4h" in event.message
    assert event.payload["bot_names"] == ["btc-4h", "eth-4h"]
    assert event.payload["oldest_heartbeat"] == seen.isoformat()
    (outbox,) = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], OutboxEvent)]
    assert outbox.correlation_id == event.correlation_id
    db.commit.assert_called_once_with()


def test_no_stalled_bot_means_no_alert_and_no_commit() -> None:
    db = _db([])

    assert worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE) == 0

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_the_same_outage_is_reported_once() -> None:
    workspace = uuid4()
    seen = NOW - timedelta(minutes=12)
    last_alert = NOW - timedelta(minutes=5)  # after the bot's last sign of life
    db = _db([(workspace, "btc-4h", seen)], last_alert=last_alert)

    assert worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE) == 0

    db.add.assert_not_called()


def test_a_bot_that_came_back_and_stalled_again_is_reported_again() -> None:
    workspace = uuid4()
    last_alert = NOW - timedelta(minutes=30)
    seen_after_alert = NOW - timedelta(minutes=10)  # alive after the alert, stale now
    db = _db([(workspace, "btc-4h", seen_after_alert)], last_alert=last_alert)

    assert worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE) == 1


def test_each_workspace_is_reported_on_its_own() -> None:
    first, second = uuid4(), uuid4()
    seen = NOW - timedelta(minutes=9)
    db = _db([(first, "a", seen), (second, "b", seen)])

    assert worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE) == 2

    assert {e.workspace_id for e in _events(db)} == {first, second}


def test_the_message_names_at_most_five_bots() -> None:
    workspace = uuid4()
    rows = [(workspace, f"bot-{i}", NOW - timedelta(minutes=9)) for i in range(7)]
    db = _db(rows)

    worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE)

    (event,) = _events(db)
    assert "bot-4" in event.message and "bot-5" not in event.message and "ほか" in event.message
    assert len(event.payload["bot_names"]) == 7  # the record keeps all of them


def test_the_query_only_looks_at_active_bots_with_an_old_stamp() -> None:
    db = _db([])

    worker_watchdog.check_trading_worker(db, now=NOW, stale_after=STALE)

    sql = str(db.execute.call_args.args[0])
    assert "trading_bot.actual_state IN" in sql and "bot_run.status IN" in sql
    assert "coalesce" in sql.lower()  # a bot that never stamped counts from its start
