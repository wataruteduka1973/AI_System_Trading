"""`build_system_status` on a real PostgreSQL: every query runs against the real schema, and the
outbox, halt and bot counts come out right. Opt-in like the other postgres tests: needs an EMPTY
dedicated worker_test_* database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import OutboxEvent, SystemEvent
from app.models.strategy import TradingHalt
from app.models.workspace import Workspace
from app.monitoring.system_status import build_system_status
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL"),
]
ROOT = Path(__file__).resolve().parents[1]


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


def _status(db, workspace_id, now):
    return build_system_status(
        db,
        workspace_id,
        now=now,
        trading_stale_after=timedelta(seconds=180),
        market_data_overdue_after=timedelta(seconds=600),
    )


def test_an_empty_workspace_is_idle_and_has_nothing_to_report(sessions) -> None:
    with sessions() as db:
        workspace = Workspace(name="empty")
        db.add(workspace)
        db.commit()

        result = _status(db, workspace.id, datetime.now(UTC))

    assert result.overall == "ok" and result.problems == []
    assert (result.trading_worker.status, result.market_data_worker.status) == ("idle", "idle")
    assert result.bots == {} and result.halts == {} and result.connections == []
    assert result.notifications.pending_events == 0


def test_halts_and_the_workspaces_own_outbox_are_counted_and_nobody_elses(sessions) -> None:
    now = datetime.now(UTC)
    with sessions() as db:
        mine, other = Workspace(name="mine"), Workspace(name="other")
        db.add_all([mine, other])
        db.flush()

        def event(workspace, status, age_minutes):
            correlation = uuid4()
            db.add(
                SystemEvent(
                    workspace_id=workspace.id,
                    severity="error",
                    category="system",
                    event_type="bot_failed",
                    source_type="test",
                    correlation_id=correlation,
                    message="m",
                    payload={},
                )
            )
            db.add(
                OutboxEvent(
                    aggregate_type="t",
                    aggregate_id=uuid4(),
                    event_type="bot_failed",
                    payload={},
                    correlation_id=correlation,
                    status=status,
                    created_at=now - timedelta(minutes=age_minutes),
                )
            )

        event(mine, "pending", 12)
        event(mine, "pending", 2)
        event(mine, "failed", 60)
        event(mine, "published", 90)
        event(other, "pending", 600)  # another workspace's backlog is not this one's
        for level, ws in (("entry_halted", mine), ("emergency_stopped", mine), ("warning", other)):
            db.add(
                TradingHalt(
                    workspace_id=ws.id,
                    scope_type="workspace",
                    level=level,
                    reason_code=f"r-{level}",
                    status="active",
                )
            )
        db.commit()

        result = _status(db, mine.id, now)

    assert result.notifications.pending_events == 2
    assert 700 <= result.notifications.oldest_pending_age_seconds <= 760  # the 12-minute one
    assert result.notifications.failed_events_last_7_days == 1
    assert result.halts == {"entry_halted": 1, "emergency_stopped": 1}
    assert result.overall == "attention"
    assert any("緊急停止中" in p for p in result.problems)
    assert any("通知が2件、5分以上配信されていません" in p for p in result.problems)
    assert any("届けられなかった通知が、直近7日に1件" in p for p in result.problems)
