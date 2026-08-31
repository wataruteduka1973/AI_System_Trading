"""Opt-in integration tests; require an EMPTY dedicated worker_test_* database.

Never use DATABASE_URL here. No exchange clients or real credentials are used.
The fixture exercises only the 0005 downgrade, before writing any new checkpoints.
"""

import asyncio
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.exchanges.types import CandlePoint, timeframe_delta
from app.market_data.application.execute_page import ExecuteMarketDataPage
from app.market_data.infrastructure.leases import FeedKey, LeaseLost, LeaseStore, WorkRef
from app.market_data.infrastructure.models import (
    MarketDataLease,
    WorkerBackfill,
    WorkerSubscription,
)
from app.market_data.infrastructure.page_access import PageAccess
from app.market_data.infrastructure.pages import PageStore
from app.models.catalog import BackfillJob, Candle, MarketDataSubscription
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


def migrate(engine, revision, downgrade=False):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def seed(connection, status="queued", enabled=True):
    workspace, instrument, job, subscription = (uuid4() for _ in range(4))
    params = {
        "ws": workspace,
        "instrument": instrument,
        "job": job,
        "subscription": subscription,
        "status": status,
        "enabled": enabled,
    }
    connection.execute(
        text("INSERT INTO fx.workspace(id,name) VALUES (:ws,'worker fixture')"), params
    )
    connection.execute(
        text("""
        INSERT INTO fx.instrument(id,exchange_id,market_id,symbol,base_asset,quote_asset,
          price_scale,quantity_scale,tick_size,step_size)
        SELECT :instrument,e.id,m.id,CAST(:instrument AS text),'BTC','JPY',2,4,0.01,0.0001
        FROM fx.exchange e CROSS JOIN fx.market m WHERE e.code='binance' AND m.code='crypto_spot'
    """),
        params,
    )
    connection.execute(
        text("""
        INSERT INTO fx.backfill_job(id,workspace_id,instrument_id,timeframe,from_time,to_time,
          trigger_type,status)
        VALUES (:job,:ws,:instrument,'1m','2026-08-01Z','2026-08-02Z','manual',:status)
    """),
        params,
    )
    connection.execute(
        text("""
        INSERT INTO fx.market_data_subscription(id,workspace_id,instrument_id,timeframe,enabled,
          last_polled_at) VALUES (:subscription,:ws,:instrument,'1m',:enabled,'2026-08-01Z')
    """),
        params,
    )
    return WorkRef(FeedKey(workspace, instrument, "1m"), "backfill", job), subscription


@pytest.fixture(scope="module")
def engine():
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
    try:
        migrate(engine, "20260825_0004")
        with engine.begin() as connection:
            for status in ("queued", "running", "validating", "succeeded", "failed", "cancelled"):
                work, _ = seed(connection, status, enabled=status != "failed")
            connection.execute(
                text("""
                INSERT INTO fx.candle(instrument_id,timeframe,open_time,close_time,
                  open,high,low,close,source)
                VALUES (:id,'1m','2026-08-01Z','2026-08-01 00:01Z',100,101,99,100,'binance')
            """),
                {"id": work.feed.instrument_id},
            )
            snapshot = {}
            for table in (
                "workspace",
                "instrument",
                "backfill_job",
                "market_data_subscription",
                "candle",
            ):
                snapshot[table] = (
                    connection.execute(text(f"SELECT * FROM fx.{table} ORDER BY id"))
                    .mappings()
                    .all()
                )
        migrate(engine, "20260831_0005")
        for downgrade in (True, False):
            with engine.connect() as connection:
                for table, before in snapshot.items():
                    after = (
                        connection.execute(text(f"SELECT * FROM fx.{table} ORDER BY id"))
                        .mappings()
                        .all()
                    )
                    assert [dict(row) for row in before] == [
                        {key: row[key] for key in before[0]} for row in after
                    ]
            if downgrade:
                migrate(engine, "20260825_0004", downgrade=True)
        migrate(engine, "20260831_0005")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM fx.candle")) == 1
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def context(engine):
    with engine.begin() as connection:
        work, subscription = seed(connection)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    return LeaseStore(sessions), sessions, work, subscription


def expire(sessions, work):
    with sessions.begin() as db:
        lease = db.get(MarketDataLease, (work.feed.workspace_id, work.feed.instrument_id, "1m"))
        now = db.scalar(select(func.clock_timestamp()))
        lease.heartbeat_at, lease.lease_until = (
            now - timedelta(seconds=2),
            now - timedelta(seconds=1),
        )


def due(sessions, work):
    with sessions.begin() as db:
        model = WorkerBackfill if work.kind == "backfill" else WorkerSubscription
        db.get(model, work.id).next_run_at = db.scalar(select(func.clock_timestamp()))


class FixtureSecrets:
    def __init__(self):
        self.calls = 0

    def get(self, ref):
        self.calls += 1
        assert ref == "fixture-only"
        return {"api_key": "synthetic", "secret_key": "synthetic"}


class FixtureCandles:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.empty = False

    async def get_candles(self, base, key, secret, symbol, timeframe, start, end):
        self.calls.append((start, end))
        if self.hook:
            self.hook()
        if self.empty:
            return []
        delta = timeframe_delta(timeframe)
        count = int((end - start) / delta)
        return [
            CandlePoint(
                start + i * delta,
                start + (i + 1) * delta,
                Decimal(100),
                Decimal(101),
                Decimal(99),
                Decimal(100),
                Decimal(2),
                1,
                True,
            )
            for i in range(count)
        ]


@pytest.fixture
def page_context(context):
    leases, sessions, work, subscription = context
    with sessions.begin() as db:
        params = {"ws": work.feed.workspace_id, "connection": uuid4(), "account": uuid4()}
        db.execute(
            text("""
          INSERT INTO fx.exchange_connection(id,workspace_id,exchange_id,label,environment,
            api_base_url,secret_ref,status)
          SELECT :connection,:ws,id,'fixture','testnet','https://testnet.binance.vision',
            'fixture-only','verified' FROM fx.exchange WHERE code='binance'
        """),
            params,
        )
        db.execute(
            text("""
          INSERT INTO fx.external_account(id,connection_id,external_account_ref_encrypted,
            external_account_ref_hash,external_account_ref_masked,environment,currency)
          VALUES (:account,:connection,'fixture','fixture','fixture','testnet','JPY')
        """),
            params,
        )
        db.execute(
            text("""
          INSERT INTO fx.workspace_account_selection(workspace_id,exchange_id,external_account_id)
          SELECT :ws,id,:account FROM fx.exchange WHERE code='binance'
        """),
            params,
        )
        job = db.get(WorkerBackfill, work.id)
        job.to_time = job.from_time + timedelta(minutes=5)
    client, secrets = FixtureCandles(), FixtureSecrets()
    pages = PageStore(leases, PageAccess(secrets, binance=client), page_size=3)
    return pages, client, secrets, sessions, work, subscription


def run_page(pages, work):
    claim = pages.leases.claim(work, uuid4())
    assert claim is not None
    return asyncio.run(ExecuteMarketDataPage(pages).execute(claim))


def candle_count(db, work):
    return db.scalar(
        select(func.count())
        .select_from(Candle)
        .where(Candle.instrument_id == work.feed.instrument_id)
    )


def test_pages_resume_overlap_and_final_validation(page_context):
    pages, client, _, sessions, work, _ = page_context
    assert run_page(pages, work) == "saved"
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.next_fetch_at == job.from_time + timedelta(minutes=3)
        assert candle_count(db, work) == 3 and job.rows_written == 3
    # A fresh application instance/claim resumes from persisted state, with one overlap.
    assert run_page(pages, work) == "saved"
    assert client.calls[1][0] == client.calls[0][1] - timedelta(minutes=1)
    assert run_page(pages, work) == "completed"
    assert len(client.calls) == 2
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.status == "succeeded" and job.attempts == 1
        assert candle_count(db, work) == 5 and job.rows_written == 6
        assert job.validation_result["coverage_status"] == "complete"
        assert job.progress_report["rows_updated"] == 1


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "30m", "1h", "4h", "1d"])
def test_all_supported_timeframes_page_and_finish(page_context, timeframe):
    pages, client, _, sessions, work, _ = page_context
    delta = timeframe_delta(timeframe)
    with sessions.begin() as db:
        job = db.get(WorkerBackfill, work.id)
        job.timeframe = timeframe
        job.to_time = job.from_time + 5 * delta
    work = replace(work, feed=replace(work.feed, timeframe=timeframe))
    assert run_page(pages, work) == "saved"
    assert run_page(pages, work) == "saved"
    assert client.calls[1][0] == client.calls[0][1] - delta
    assert run_page(pages, work) == "completed"
    with sessions() as db:
        assert candle_count(db, work) == 5
        assert db.get(WorkerBackfill, work.id).validation_result["coverage_status"] == "complete"


def test_page_commit_failure_rolls_back_and_refetches(page_context, monkeypatch):
    pages, client, _, sessions, work, _ = page_context
    from app.market_data.infrastructure import pages as page_module

    original = page_module.restore_report

    def crash(_target):
        raise DBAPIError("fixture", {}, RuntimeError("connection lost"))

    monkeypatch.setattr(page_module, "restore_report", crash)
    with pytest.raises(DBAPIError):
        run_page(pages, work)
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.next_fetch_at is None and job.rows_written == 0
        assert candle_count(db, work) == 0
    expire(sessions, work)
    pages.leases.recover_expired()
    due(sessions, work)
    monkeypatch.setattr(page_module, "restore_report", original)
    assert run_page(pages, work) == "saved"
    assert client.calls[0] == client.calls[1]


@pytest.mark.parametrize("before_commit", [True, False])
def test_real_process_exit_before_or_after_page_commit(page_context, before_commit):
    pages, _, _, sessions, work, _ = page_context
    program = f"""
import sys, os, asyncio
sys.path[:] = {sys.path!r}
from uuid import UUID, uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.market_data.infrastructure.leases import FeedKey, WorkRef, LeaseStore
from app.market_data.infrastructure.page_access import PageAccess
from app.market_data.infrastructure.pages import PageStore
from app.market_data.application.execute_page import ExecuteMarketDataPage
from app.services.market_data import CandleIngestionService
from test_worker_leases_postgres import FixtureSecrets, FixtureCandles
engine = create_engine({TEST_URL!r})
leases = LeaseStore(sessionmaker(engine, autoflush=False, expire_on_commit=False))
pages = PageStore(leases, PageAccess(FixtureSecrets(), binance=FixtureCandles()), page_size=3)
work = WorkRef(FeedKey(UUID('{work.feed.workspace_id}'), UUID('{work.feed.instrument_id}'), '1m'),
               'backfill', UUID('{work.id}'))
if {before_commit!r}:
    original = CandleIngestionService._upsert_points
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(17)
    CandleIngestionService._upsert_points = interrupted
outcome = asyncio.run(ExecuteMarketDataPage(pages).execute(leases.claim(work, uuid4())))
os._exit(18 if outcome == 'saved' else 19)
"""
    child = subprocess.run([sys.executable, "-c", program], capture_output=True, timeout=20)
    assert child.returncode == (17 if before_commit else 18), child.stderr.decode(errors="replace")
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert candle_count(db, work) == (0 if before_commit else 3)
        assert job.rows_written == (0 if before_commit else 3)
    if before_commit:
        expire(sessions, work)
        pages.leases.recover_expired()
        due(sessions, work)
    assert run_page(pages, work) == "saved"
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.next_fetch_at == job.from_time + timedelta(minutes=3 if before_commit else 5)


@pytest.mark.parametrize("mutation", ["expired", "stopped", "account", "workspace", "secret"])
def test_page_late_response_cannot_write(page_context, mutation):
    pages, client, _, sessions, work, subscription = page_context
    if mutation == "stopped":
        work = WorkRef(work.feed, "polling", subscription)

    def mutate():
        if mutation == "expired":
            expire(sessions, work)
            return
        with sessions.begin() as db:
            if mutation == "stopped":
                db.get(WorkerSubscription, subscription).enabled = False
            elif mutation == "workspace":
                db.execute(
                    text("UPDATE fx.workspace SET status='suspended' WHERE id=:ws"),
                    {"ws": work.feed.workspace_id},
                )
            elif mutation == "secret":
                db.execute(
                    text(
                        "UPDATE fx.exchange_connection SET secret_ref='replaced' "
                        "WHERE workspace_id=:ws"
                    ),
                    {"ws": work.feed.workspace_id},
                )
            else:
                db.execute(
                    text("DELETE FROM fx.workspace_account_selection WHERE workspace_id=:ws"),
                    {"ws": work.feed.workspace_id},
                )

    client.hook = mutate
    result = run_page(pages, work)
    assert result == ("discarded" if mutation in ("expired", "stopped") else "failed")
    with sessions() as db:
        assert candle_count(db, work) == 0
        if mutation == "stopped":
            target = db.get(WorkerSubscription, subscription)
            assert not target.enabled and target.last_success_at is None


def test_stopped_before_request_does_not_decrypt(page_context):
    pages, client, secrets, sessions, work, subscription = page_context
    work = WorkRef(work.feed, "polling", subscription)
    claim = pages.leases.claim(work, uuid4())
    with sessions.begin() as db:
        db.get(WorkerSubscription, subscription).enabled = False
    assert asyncio.run(ExecuteMarketDataPage(pages).execute(claim)) == "discarded"
    assert not client.calls and secrets.calls == 0


def test_empty_pages_preserve_partial_coverage(page_context):
    pages, client, _, sessions, work, _ = page_context
    client.empty = True
    assert run_page(pages, work) == "saved"
    assert run_page(pages, work) == "saved"
    assert run_page(pages, work) == "completed"
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.validation_result["coverage_status"] == "empty"
        assert job.progress_report["empty_source_window_count"] == 2


def test_retry_limit_preserves_checkpoint_and_redacts_errors(page_context):
    pages, client, _, sessions, work, _ = page_context
    assert run_page(pages, work) == "saved"
    checkpoint = client.calls[-1][1]

    def fail():
        raise TimeoutError("DO-NOT-PERSIST synthetic credentials")

    client.hook = fail
    for attempt in range(3):
        due(sessions, work)
        assert run_page(pages, work) == "failed"
        with sessions() as db:
            job = db.get(WorkerBackfill, work.id)
            assert job.next_fetch_at == checkpoint
            assert job.consecutive_failures == attempt + 1
            assert job.status == ("failed" if attempt == 2 else "queued")
            assert job.error_code == "communication_failed"
            assert "DO-NOT-PERSIST" not in str(job.progress_report)


def test_polling_fixed_window_finishes_and_schedules(page_context):
    pages, client, _, sessions, work, subscription = page_context
    work = WorkRef(work.feed, "polling", subscription)
    assert run_page(pages, work) == "saved"
    with sessions() as db:
        target = db.get(WorkerSubscription, subscription)
        assert target.next_fetch_at is None and target.scan_to is None
        assert target.last_success_at is not None
        assert target.next_run_at > target.last_success_at
        assert target.enabled


def test_cancel_during_request_releases_without_failure(page_context):
    pages, client, _, sessions, work, _ = page_context

    def cancel():
        raise asyncio.CancelledError()

    client.hook = cancel
    with pytest.raises(asyncio.CancelledError):
        run_page(pages, work)
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.next_fetch_at is None and job.consecutive_failures == 0
    client.hook = None
    assert run_page(pages, work) == "saved"


def test_polling_permanent_error_blocks_without_changing_enabled(page_context):
    pages, client, _, sessions, work, subscription = page_context
    work = WorkRef(work.feed, "polling", subscription)

    def fail():
        raise RuntimeError("synthetic-sensitive-value")

    client.hook = fail
    assert run_page(pages, work) == "failed"
    with sessions() as db:
        target = db.get(WorkerSubscription, subscription)
        assert target.enabled and target.blocked_reason == "internal_error"
        assert target.next_fetch_at is not None
        assert target.last_success_at is None
        records = db.execute(
            text("SELECT after_data FROM fx.audit_log WHERE resource_id=:id"), {"id": work.id}
        ).all()
        assert "synthetic-sensitive-value" not in str(records)
    assert pages.leases.claim(work, uuid4()) is None


def test_wrong_workspace_selection_never_decrypts(page_context):
    pages, client, secrets, sessions, work, _ = page_context
    with sessions.begin() as db:
        other = uuid4()
        db.execute(
            text("INSERT INTO fx.workspace(id,name) VALUES (:id,'other fixture')"), {"id": other}
        )
        db.execute(
            text("UPDATE fx.exchange_connection SET workspace_id=:other WHERE workspace_id=:ws"),
            {"other": other, "ws": work.feed.workspace_id},
        )
    assert run_page(pages, work) == "failed"
    assert not client.calls and secrets.calls == 0


def test_polling_catchup_resumes_fixed_window(page_context):
    pages, client, _, sessions, work, subscription = page_context
    work = WorkRef(work.feed, "polling", subscription)
    with sessions.begin() as db:
        target = db.get(WorkerSubscription, subscription)
        target.next_fetch_at = datetime(2026, 8, 1, tzinfo=UTC)
        target.scan_to = target.next_fetch_at + timedelta(minutes=4)
        endpoint = target.scan_to
    assert run_page(pages, work) == "saved"
    with sessions() as db:
        target = db.get(WorkerSubscription, subscription)
        assert target.scan_to == endpoint and target.next_fetch_at < endpoint
    assert run_page(pages, work) == "saved"
    assert client.calls[-1][1] == endpoint
    with sessions() as db:
        assert db.get(WorkerSubscription, subscription).scan_to is None


def test_final_validation_resume_without_fetch(page_context, monkeypatch):
    pages, client, _, sessions, work, _ = page_context
    assert run_page(pages, work) == run_page(pages, work) == "saved"
    from app.market_data.infrastructure import pages as page_module

    original = page_module.build_candle_coverage

    def crash(*args):
        raise DBAPIError("fixture", {}, RuntimeError("connection lost"))

    monkeypatch.setattr(page_module, "build_candle_coverage", crash)
    with pytest.raises(DBAPIError):
        run_page(pages, work)
    expire(sessions, work)
    pages.leases.recover_expired()
    due(sessions, work)
    monkeypatch.setattr(page_module, "build_candle_coverage", original)
    assert run_page(pages, work) == "completed"
    assert len(client.calls) == 2


def test_year_of_minute_candles_validation_budget(page_context):
    pages, client, _, sessions, work, _ = page_context
    with sessions.begin() as db:
        job = db.get(WorkerBackfill, work.id)
        job.from_time = datetime(2025, 8, 1, tzinfo=UTC)
        job.to_time = job.next_fetch_at = datetime(2026, 8, 1, tzinfo=UTC)
        db.execute(
            text("""
          INSERT INTO fx.candle(instrument_id,timeframe,open_time,close_time,
            open,high,low,close,source,is_final)
          SELECT :id,'1m',t,t+interval '1 minute',100,101,99,100,'binance',true
          FROM generate_series(CAST(:start AS timestamptz),
            CAST(:end AS timestamptz)-interval '1 minute', interval '1 minute') t
        """),
            {"id": work.feed.instrument_id, "start": job.from_time, "end": job.to_time},
        )
    started = time.monotonic()
    assert run_page(pages, work) == "completed"
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"Final validation exceeded transaction budget: {elapsed:.2f}s"
    assert not client.calls


def test_migration_roundtrip_and_legacy_mapping(engine):
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260831_0005"
    assert "next_fetch_at" not in BackfillJob.__table__.c
    assert "blocked_reason" not in MarketDataSubscription.__table__.c


def test_claim_heartbeat_and_graceful_release(context):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    assert claim is not None
    assert store.heartbeat(claim)
    assert store.claim(work, uuid4()) is None
    assert store.release(claim)
    assert not store.release(claim)
    again = store.claim(work, uuid4())
    assert again.token != claim.token
    with sessions() as db:
        assert db.get(WorkerBackfill, work.id).attempts == 1
    store.release(again)


def test_two_connections_compete_for_first_claim(context):
    store, _, work, _ = context
    barrier = Barrier(2)

    def compete():
        barrier.wait(timeout=5)
        return store.claim(work, uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: compete(), range(2)))
    claims = [claim for claim in results if claim is not None]
    assert len(claims) == 1
    store.release(claims[0])


def test_polling_and_backfill_share_feed_ownership(context):
    store, _, work, subscription = context
    claim = store.claim(work, uuid4())
    polling = WorkRef(work.feed, "polling", subscription)
    assert store.claim(polling, uuid4()) is None
    store.release(claim)
    assert store.release(store.claim(polling, uuid4()))


def test_wrong_workspace_or_instrument_cannot_claim_target(context, engine):
    store, _, work, subscription = context
    with engine.begin() as connection:
        other, _ = seed(connection)
    assert store.claim(replace(work, feed=other.feed), uuid4()) is None
    assert store.claim(WorkRef(other.feed, "polling", subscription), uuid4()) is None


def test_guarded_checkpoint_and_business_write_commit_together(context):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    with store.guarded_write(claim) as (db, target):
        target.next_fetch_at = target.from_time + timedelta(minutes=1)
        target.rows_written += 1
        db.execute(
            text("UPDATE fx.workspace SET name='committed' WHERE id=:id"),
            {"id": work.feed.workspace_id},
        )
    with sessions() as db:
        assert db.get(WorkerBackfill, work.id).rows_written == 1
        assert (
            db.scalar(
                text("SELECT name FROM fx.workspace WHERE id=:id"), {"id": work.feed.workspace_id}
            )
            == "committed"
        )
    assert not store.heartbeat(claim)


def test_guarded_failure_rolls_back_every_write_and_keeps_lease(context):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    with pytest.raises(RuntimeError, match="injected"), store.guarded_write(claim) as (db, target):
        target.next_fetch_at = target.to_time
        target.rows_written += 1
        db.execute(
            text("UPDATE fx.workspace SET name='wrong' WHERE id=:id"),
            {"id": work.feed.workspace_id},
        )
        raise RuntimeError("injected")
    with sessions() as db:
        target = db.get(WorkerBackfill, work.id)
        assert target.next_fetch_at is None and target.rows_written == 0
        assert (
            db.scalar(
                text("SELECT name FROM fx.workspace WHERE id=:id"), {"id": work.feed.workspace_id}
            )
            == "worker fixture"
        )
    assert store.release(claim)


def test_expired_claim_cannot_extend_release_or_write(context):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    expire(sessions, work)
    assert not store.heartbeat(claim)
    assert not store.release(claim)
    with pytest.raises(LeaseLost), store.guarded_write(claim):
        pytest.fail("stale owner must not receive a writable session")
    assert store.recover_expired() == 1
    due(sessions, work)
    successor = store.claim(work, uuid4())
    assert successor.token != claim.token
    assert not store.release(claim)
    with pytest.raises(LeaseLost), store.guarded_write(claim):
        pytest.fail("old owner must not affect successor")
    store.release(successor)


def test_inline_recovery_commits_retry_before_reclaim(context):
    store, sessions, work, _ = context
    store.claim(work, uuid4())
    expire(sessions, work)
    assert store.claim(work, uuid4()) is None
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.status == "queued" and job.consecutive_failures == 1
        assert job.next_run_at > db.scalar(select(func.clock_timestamp()))


@pytest.mark.parametrize("kind", ["backfill", "polling"])
def test_three_expirations_stop_automatic_retry(context, kind):
    store, sessions, work, subscription = context
    if kind == "polling":
        work = WorkRef(work.feed, kind, subscription)
    for _ in range(3):
        assert store.claim(work, uuid4()) is not None
        expire(sessions, work)
        assert store.recover_expired() == 1
        due(sessions, work)
    assert store.claim(work, uuid4()) is None
    with sessions() as db:
        if kind == "backfill":
            target = db.get(WorkerBackfill, work.id)
            assert target.status == "failed" and target.finished_at is not None
        else:
            target = db.get(WorkerSubscription, work.id)
            assert target.enabled and target.blocked_reason == "worker_interrupted"
        assert target.consecutive_failures == 3


def test_disabled_subscription_is_not_resurrected(context):
    store, sessions, work, subscription = context
    polling = WorkRef(work.feed, "polling", subscription)
    claim = store.claim(polling, uuid4())
    with sessions.begin() as db:
        db.get(WorkerSubscription, subscription).enabled = False
    with pytest.raises(LeaseLost), store.guarded_write(claim):
        pytest.fail("disabled feed must not receive writes")
    expire(sessions, polling)
    store.recover_expired()
    assert store.claim(polling, uuid4()) is None
    with sessions() as db:
        target = db.get(WorkerSubscription, subscription)
        assert not target.enabled and target.consecutive_failures == 0


def test_guarded_transaction_blocks_recovery_and_skips_claim(context):
    store, _, work, _ = context
    short_store = LeaseStore(store.sessions, lease_seconds=1)
    claim = short_store.claim(work, uuid4())
    with short_store.guarded_write(claim):
        time.sleep(1.1)
        assert store.recover_expired() == 0
        assert store.claim(work, uuid4()) is None


@pytest.mark.parametrize(
    "mutation", ["partial_owner", "bad_cursor", "negative_failures", "array_report"]
)
def test_database_constraints_reject_invalid_state(context, mutation):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    with pytest.raises(IntegrityError), sessions.begin() as db:
        if mutation == "partial_owner":
            db.get(
                MarketDataLease, (work.feed.workspace_id, work.feed.instrument_id, "1m")
            ).owner_id = None
        else:
            target = db.get(WorkerBackfill, work.id)
            if mutation == "bad_cursor":
                target.next_fetch_at = target.to_time + timedelta(seconds=1)
            elif mutation == "negative_failures":
                target.consecutive_failures = -1
            else:
                target.progress_report = []
    store.release(claim)


def test_process_death_recovers_committed_claim(context):
    store, sessions, work, _ = context
    program = f"""
import sys, time
sys.path[:] = {sys.path!r}
from uuid import UUID, uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.market_data.infrastructure.leases import FeedKey, WorkRef, LeaseStore
engine = create_engine({TEST_URL!r})
store = LeaseStore(sessionmaker(engine), lease_seconds=1)
work = WorkRef(FeedKey(UUID('{work.feed.workspace_id}'), UUID('{work.feed.instrument_id}'), '1m'),
               'backfill', UUID('{work.id}'))
assert store.claim(work, uuid4()) is not None
print('claimed', flush=True)
time.sleep(30)
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Bound the handshake; do not hang the suite on a failing child startup.
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                line = pool.submit(process.stdout.readline).result(timeout=10)
            except TimeoutError:
                process.kill()
                raise
        assert line.strip() == "claimed"
    finally:
        process.kill()
        process.wait(timeout=5)
    time.sleep(1.1)
    assert store.recover_expired() == 1
    due(sessions, work)
    assert store.release(store.claim(work, uuid4()))


def test_two_worker_processes_get_only_one_token(context):
    store, sessions, work, _ = context
    program = f"""
import sys
sys.path[:] = {sys.path!r}
from uuid import UUID, uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.market_data.infrastructure.leases import FeedKey, WorkRef, LeaseStore
engine = create_engine({TEST_URL!r})
store = LeaseStore(sessionmaker(engine))
work = WorkRef(FeedKey(UUID('{work.feed.workspace_id}'), UUID('{work.feed.instrument_id}'), '1m'),
               'backfill', UUID('{work.id}'))
sys.stdin.readline()
print('claimed' if store.claim(work, uuid4()) else 'busy', flush=True)
engine.dispose()
"""
    processes = []
    try:
        for _ in range(2):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-u", "-c", program],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = [process.communicate(timeout=10) for process in processes]
        assert all(process.returncode == 0 for process in processes)
        assert sorted(stdout.strip() for stdout, _ in results) == ["busy", "claimed"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    expire(sessions, work)
    store.recover_expired()


def test_cursor_change_and_wrong_owner_are_fenced(context):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    wrong = replace(claim, owner_id=uuid4())
    assert not store.heartbeat(wrong) and not store.release(wrong)
    with sessions.begin() as db:
        job = db.get(WorkerBackfill, work.id)
        job.next_fetch_at = job.from_time + timedelta(minutes=1)
    with pytest.raises(LeaseLost), store.guarded_write(claim):
        pytest.fail("changed checkpoint must not be overwritten")
    assert store.release(claim)


def test_audit_records_never_contain_lease_token(context):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    expire(sessions, work)
    store.recover_expired()
    with sessions() as db:
        records = db.execute(
            text("SELECT workspace_id, action, after_data FROM fx.audit_log WHERE resource_id=:id"),
            {"id": work.id},
        ).all()
    assert len(records) == 2
    assert all(row.workspace_id == work.feed.workspace_id for row in records)
    assert str(claim.token) not in str(records)
    recovery = next(row for row in records if row.action == "market_data.lease_recovered")
    assert recovery.after_data["error_code"] == "worker_interrupted"


def test_database_disconnect_rolls_back_checkpoint_then_recovers(context, engine):
    store, sessions, work, _ = context
    claim = store.claim(work, uuid4())
    with pytest.raises(DBAPIError), store.guarded_write(claim) as (db, target):
        target.next_fetch_at = target.to_time
        target.rows_written = 123
        db.flush()
        # Terminate only the exact connection opened above, on the dedicated test DB.
        backend_pid = db.scalar(text("SELECT pg_backend_pid()"))
        with engine.begin() as killer:
            assert killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid})
    with sessions() as db:
        job = db.get(WorkerBackfill, work.id)
        assert job.next_fetch_at is None and job.rows_written == 0
    expire(sessions, work)
    assert store.recover_expired() == 1
    assert not store.heartbeat(claim)
