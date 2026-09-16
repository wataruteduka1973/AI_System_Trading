"""Opt-in integration tests for the (4) runner/candidate-discovery layer.

Same convention as test_worker_leases_postgres.py: requires an EMPTY dedicated
worker_test_* database via WORKER_TEST_DATABASE_URL. Reuses that file's engine/context
fixtures and fakes instead of duplicating schema setup.
"""

# ruff: noqa: F811  (test functions intentionally shadow imported pytest fixtures by name)

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.market_data.application.execute_page import ExecuteMarketDataPage
from app.market_data.infrastructure.candidates import CandidateScanner
from app.market_data.infrastructure.models import WorkerBackfill
from app.market_data.infrastructure.normalize import normalize_legacy_jobs
from app.market_data.worker.runner import WorkerRunner
from app.models.audit import AuditLog
from app.services.market_data import DuplicateBackfillError, ensure_no_overlapping_backfill
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from test_worker_leases_postgres import (  # noqa: F401  (reused as pytest fixtures/helpers)
    candle_count,
    context,
    migrate,
    page_context,
    seed,
)

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL")


@pytest.fixture
def engine():
    """A fresh, uniquely-named database per test.

    test_worker_leases_postgres.py's own `engine` fixture is module-scoped and shared by
    all its tests; importing it here would run it a second time against the same physical
    database. This file's tests also make DB-wide queries (CandidateScanner, normalize),
    so unlike that file's per-feed assertions they cannot tolerate leftover rows from
    other tests either -- each test gets its own throwaway `<name>_runner_<random>` database,
    created and dropped around the test.
    """
    base_url = make_url(TEST_URL)
    if not (base_url.database or "").startswith("worker_test_"):
        pytest.fail("Refusing non-test database; name must start with worker_test_")
    target_database = f"{base_url.database}_runner_{uuid4().hex[:8]}"
    bootstrap = create_engine(
        base_url, connect_args={"connect_timeout": 5}, isolation_level="AUTOCOMMIT"
    )
    try:
        with bootstrap.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{target_database}"'))
        engine = create_engine(
            base_url.set(database=target_database), connect_args={"connect_timeout": 5}
        )
        try:
            migrate(engine, "20260831_0005")
            yield engine
        finally:
            engine.dispose()
    finally:
        with bootstrap.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target_database}"'))
        bootstrap.dispose()


def test_candidate_scanner_orders_due_work_and_filters_ineligible_rows(engine):
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as connection:
        f1, s1 = seed(connection)
        f2, s2 = seed(connection)
        f3, s3 = seed(connection)
        f4, s4 = seed(connection)

        connection.execute(
            text(
                "UPDATE fx.backfill_job SET next_run_at = clock_timestamp() - interval '30 seconds'"
                " WHERE id = :id"
            ),
            {"id": f1.id},
        )
        connection.execute(
            text("UPDATE fx.market_data_subscription SET enabled = false WHERE id = :id"),
            {"id": s1},
        )

        connection.execute(
            text("UPDATE fx.backfill_job SET status = 'succeeded' WHERE id = :id"), {"id": f2.id}
        )
        connection.execute(
            text(
                "UPDATE fx.market_data_subscription SET next_run_at ="
                " clock_timestamp() - interval '20 seconds' WHERE id = :id"
            ),
            {"id": s2},
        )

        connection.execute(
            text(
                "UPDATE fx.backfill_job SET next_run_at = clock_timestamp() + interval '1 hour'"
                " WHERE id = :id"
            ),
            {"id": f3.id},
        )
        connection.execute(
            text(
                "UPDATE fx.market_data_subscription SET blocked_reason = 'authentication_failed'"
                " WHERE id = :id"
            ),
            {"id": s3},
        )

        connection.execute(
            text(
                "UPDATE fx.backfill_job SET next_run_at = clock_timestamp() - interval '40 seconds'"
                " WHERE id = :id"
            ),
            {"id": f4.id},
        )
        connection.execute(
            text("UPDATE fx.market_data_subscription SET enabled = false WHERE id = :id"),
            {"id": s4},
        )

    result = CandidateScanner(sessions).due(10)
    assert [ref.id for ref in result] == [f4.id, f1.id, s2]
    assert [ref.kind for ref in result] == ["backfill", "backfill", "polling"]
    assert [ref.id for ref in CandidateScanner(sessions).due(1)] == [f4.id]


def test_candidate_scanner_still_finds_a_backfill_mid_resume(engine):
    """A job claimed for its first page flips to 'running' and must stay discoverable so the
    runner can resume its remaining pages on later scan cycles (regression for a bug where
    the scanner only matched status='queued', starving any backfill after its first page)."""
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as connection:
        running, subscription_id = seed(connection, status="running")
        connection.execute(
            text(
                "UPDATE fx.backfill_job SET next_run_at = clock_timestamp() - interval '5 seconds'"
                " WHERE id = :id"
            ),
            {"id": running.id},
        )
        connection.execute(
            text("UPDATE fx.market_data_subscription SET enabled = false WHERE id = :id"),
            {"id": subscription_id},
        )

    assert [ref.id for ref in CandidateScanner(sessions).due(10)] == [running.id]


def test_normalize_legacy_jobs_resets_stale_rows_and_skips_already_migrated(engine):
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as connection:
        stale, _ = seed(connection, status="running")
        migrated, _ = seed(connection, status="validating")
        connection.execute(
            text(
                "UPDATE fx.backfill_job SET next_fetch_at = from_time + interval '1 minute'"
                " WHERE id = :id"
            ),
            {"id": migrated.id},
        )

    with sessions() as db:
        result = normalize_legacy_jobs(db)
    assert result.reset_count == 1
    assert result.skipped_already_migrated_count == 1

    with sessions() as db:
        stale_job = db.get(WorkerBackfill, stale.id)
        migrated_job = db.get(WorkerBackfill, migrated.id)
        assert stale_job.status == "queued"
        assert stale_job.finished_at is None
        assert migrated_job.status == "validating"
        audits = db.scalars(select(AuditLog).where(AuditLog.resource_id == stale.id)).all()
        assert any(a.action == "market_data.legacy_job_normalized" for a in audits)

    with sessions() as db:
        again = normalize_legacy_jobs(db)
    assert again.reset_count == 0


def test_overlap_check_no_longer_fails_a_lease_owned_running_job(context):
    leases, sessions, work, _ = context
    claim = leases.claim(work, uuid4())
    assert claim is not None

    with sessions.begin() as db, pytest.raises(DuplicateBackfillError):
        ensure_no_overlapping_backfill(
            db,
            workspace_id=work.feed.workspace_id,
            instrument_id=work.feed.instrument_id,
            timeframe="1m",
            requested_from=datetime(2026, 8, 1, tzinfo=UTC),
            requested_to=datetime(2026, 8, 2, tzinfo=UTC),
        )

    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.status == "running"


def test_runner_end_to_end_claims_pages_and_completes_a_real_backfill(page_context):
    pages, _client, _secrets, sessions, work, _subscription = page_context
    scanner = CandidateScanner(sessions)
    runner = WorkerRunner(
        pages.leases,
        ExecuteMarketDataPage(pages),
        scanner.due,
        scan_interval_seconds=0.01,
        heartbeat_interval_seconds=0.05,
        candidate_limit=10,
    )
    asyncio.run(runner.run(asyncio.Event(), max_cycles=30))

    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.status == "succeeded"
        assert job.rows_written > 0
        assert candle_count(db, work) > 0
