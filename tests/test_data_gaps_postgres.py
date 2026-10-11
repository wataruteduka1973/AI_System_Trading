"""The missing-candle report and the gap backfill against a real PostgreSQL
(app/market_data/application/gap_reports.py, use_cases.enqueue_gap_backfill): which gaps a
workspace sees, the filters, the summary, the latest job and the bots on each gap, and what filling
a gap queues. Opt-in like the other postgres tests: needs an EMPTY dedicated worker_test_*
database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.market_data.application import gap_reports
from app.market_data.application import use_cases as market_data_application
from app.market_data.application.use_cases import MarketDataApplicationError
from app.models.audit import AuditLog
from app.models.connections import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Market,
    WorkspaceAccountSelection,
)
from app.models.instruments import Instrument
from app.models.market_data import BackfillJob, MarketDataGap
from app.models.trading import TradingAccount
from app.models.workspace import Workspace
from app.trading.application.paper_provisioning import create_approved_bot
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL"),
]
ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


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
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE fx.workspace, fx.instrument, fx.market_data_gap, fx.outbox_event, "
                "fx.system_event CASCADE"
            )
        )
    _migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.commit()
    engine.dispose()


def _instrument(db, exchange_code, symbol):
    exchange = db.scalar(select(Exchange).where(Exchange.code == exchange_code))
    market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
    instrument = Instrument(
        exchange_id=exchange.id,
        market_id=market.id,
        symbol=f"{symbol}{uuid4().hex[:4]}",
        base_asset="BTC",
        quote_asset="USDT",
        price_scale=2,
        quantity_scale=4,
        tick_size=1,
        step_size=1,
    )
    db.add(instrument)
    db.flush()
    return exchange, instrument


def _gap(db, instrument, *, timeframe="1h", status="open", hours_ago=48, missing=5):
    gap = MarketDataGap(
        instrument_id=instrument.id,
        timeframe=timeframe,
        from_time=START - timedelta(hours=hours_ago),
        to_time=START - timedelta(hours=hours_ago - missing),
        expected_count=missing,
        missing_count=missing,
        reason_code="internal_missing_candles",
        status=status,
    )
    db.add(gap)
    db.flush()
    return gap


@pytest.fixture
def world(sessions):
    """A workspace with a selected Binance account (so its instrument is collectable), a gap on
    it, a public research instrument with gaps, and a second workspace that sees neither."""
    with sessions() as db:
        # The public history is visible to every workspace, so each test starts from no gaps and
        # no jobs of earlier tests.
        db.execute(text("TRUNCATE fx.market_data_gap, fx.backfill_job CASCADE"))
        workspace = Workspace(name=f"gaps-{uuid4().hex[:6]}")
        stranger = Workspace(name=f"stranger-{uuid4().hex[:6]}")
        db.add_all([workspace, stranger])
        db.flush()
        exchange, own = _instrument(db, "binance", "OWN")
        _, research = _instrument(db, "binance_public", "PUB")
        connection = ExchangeConnection(
            workspace_id=workspace.id,
            exchange_id=exchange.id,
            label="gaps",
            environment="testnet",
            api_base_url="https://example.invalid",
            status="verified",
            secret_ref="test-secret",
            last_verified_at=START,
            credentials_updated_at=START,
        )
        db.add(connection)
        db.flush()
        account = ExternalAccount(
            connection_id=connection.id,
            external_account_ref_encrypted="enc",
            external_account_ref_hash=uuid4().hex,
            external_account_ref_masked="****",
            environment="testnet",
            currency="USDT",
            status="active",
        )
        db.add(account)
        db.flush()
        db.add(
            WorkspaceAccountSelection(
                workspace_id=workspace.id, exchange_id=exchange.id, external_account_id=account.id
            )
        )
        open_gap = _gap(db, own, timeframe="1h", status="open", hours_ago=48, missing=5)
        _gap(db, own, timeframe="1h", status="resolved", hours_ago=200, missing=3)
        _gap(db, own, timeframe="4h", status="open", hours_ago=96, missing=2)
        _gap(db, research, timeframe="1h", status="ignored", hours_ago=300, missing=7)
        research_open = _gap(db, research, timeframe="1h", status="open", hours_ago=400, missing=4)
        db.commit()
        return {
            "workspace": workspace.id,
            "stranger": stranger.id,
            "own": own.id,
            "research": research.id,
            "open_gap": open_gap.id,
            "research_open_gap": research_open.id,
            "connection": connection.id,
        }


def _list(sessions, workspace_id, **kwargs):
    defaults = dict(statuses=[], instrument_id=None, timeframe=None, limit=50)
    with sessions() as db:
        return gap_reports.list_gaps(db, workspace_id, **{**defaults, **kwargs})


def test_a_workspace_sees_its_own_instruments_and_the_public_ones_only(sessions, world) -> None:
    total, rows = _list(sessions, world["workspace"])
    stranger_total, stranger_rows = _list(sessions, world["stranger"])

    assert total == 5
    assert {row.gap.instrument_id for row in rows} == {world["own"], world["research"]}
    assert stranger_total == 2  # the public history is open to every workspace
    assert {row.exchange_code for row in stranger_rows} == {"binance_public"}


def test_the_filters_narrow_the_list(sessions, world) -> None:
    def symbols(**kwargs):
        return [
            (r.gap.timeframe, r.gap.status)
            for r in _list(sessions, world["workspace"], **kwargs)[1]
        ]

    assert len(symbols(statuses=["open"])) == 3
    assert len(symbols(statuses=["ignored", "resolved"])) == 2
    assert {tf for tf, _ in symbols(timeframe="4h")} == {"4h"}
    total, rows = _list(sessions, world["workspace"], instrument_id=world["own"])
    assert total == 3 and {r.symbol[:3] for r in rows} == {"OWN"}


def test_the_list_is_newest_first_and_pages(sessions, world) -> None:
    total, first = _list(sessions, world["workspace"], limit=2)
    _, second = _list(sessions, world["workspace"], limit=2, offset=2)

    assert total == 5 and len(first) == 2 and len(second) == 2
    times = [row.gap.from_time for row in first + second]
    assert times == sorted(times, reverse=True)


def test_only_an_open_gap_of_a_collected_instrument_can_be_filled(sessions, world) -> None:
    _, rows = _list(sessions, world["workspace"])
    by_gap = {row.gap.id: row for row in rows}

    assert by_gap[world["open_gap"]].can_backfill is True
    assert by_gap[world["research_open_gap"]].can_backfill is False  # public history
    assert all(not row.can_backfill for row in rows if row.gap.status != "open")


def test_the_summary_counts_each_series_with_the_open_ones_first(sessions, world) -> None:
    with sessions() as db:
        summary = gap_reports.summarize_gaps(db, world["workspace"])

    own_1h = next(s for s in summary if s.instrument_id == world["own"] and s.timeframe == "1h")
    assert (own_1h.open_count, own_1h.open_missing, own_1h.resolved_count) == (1, 5, 1)
    research = next(s for s in summary if s.instrument_id == world["research"])
    assert (research.open_count, research.ignored_count) == (1, 1)
    missing = [s.open_missing for s in summary]
    assert missing == sorted(missing, reverse=True)


def test_the_bots_running_on_the_instrument_are_named(sessions, world) -> None:
    with sessions() as db:
        connection = db.get(ExchangeConnection, world["connection"])
        instrument = db.get(Instrument, world["own"])
        account = TradingAccount(
            workspace_id=world["workspace"],
            connection_id=connection.id,
            mode="paper",
            base_currency="USDT",
        )
        db.add(account)
        db.commit()
        running = create_approved_bot(
            db, world["workspace"], account, instrument, bot_name="runner"
        )
        running.actual_state = "running"
        stopped_account = TradingAccount(
            workspace_id=world["workspace"],
            connection_id=connection.id,
            mode="paper",
            base_currency="USDT",
        )
        db.add(stopped_account)
        db.commit()
        create_approved_bot(db, world["workspace"], stopped_account, instrument, bot_name="idle")
        db.commit()

    _, rows = _list(sessions, world["workspace"], instrument_id=world["own"])

    assert {tuple(row.affected_bots) for row in rows} == {("runner",)}


def _fill(sessions, world, gap_id=None):
    with sessions() as db:
        return market_data_application.enqueue_gap_backfill(
            db, world["workspace"], gap_id or world["open_gap"], lambda *_: None
        )


def test_filling_a_gap_queues_a_job_for_its_stretch_padded_by_one_candle(sessions, world) -> None:
    job = _fill(sessions, world)

    with sessions() as db:
        gap = db.get(MarketDataGap, world["open_gap"])
        assert job.gap_id == gap.id and job.trigger_type == "manual" and job.status == "queued"
        assert job.from_time == gap.from_time - timedelta(hours=1)
        assert job.to_time == gap.to_time + timedelta(hours=1)
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "candle.gap_backfill_queued"))
        assert audit is not None and audit.resource_id == job.id

    _, rows = _list(sessions, world["workspace"], instrument_id=world["own"], statuses=["open"])
    row = next(r for r in rows if r.gap.id == world["open_gap"])
    assert row.latest_job is not None and row.latest_job.id == job.id
    assert row.can_backfill is False  # a job is already queued for it


def test_a_second_request_while_one_is_queued_is_refused(sessions, world) -> None:
    _fill(sessions, world)

    with pytest.raises(MarketDataApplicationError) as refused:
        _fill(sessions, world)

    assert refused.value.code == "overlapping_backfill"


def test_a_public_history_gap_a_resolved_gap_and_a_missing_gap_are_refused(sessions, world) -> None:
    with pytest.raises(MarketDataApplicationError) as research:
        _fill(sessions, world, world["research_open_gap"])
    assert research.value.code == "research_instrument"

    with sessions() as db:
        gap = db.get(MarketDataGap, world["open_gap"])
        gap.status = "resolved"
        db.commit()
    with pytest.raises(MarketDataApplicationError) as resolved:
        _fill(sessions, world)
    assert resolved.value.code == "gap_not_open"

    with pytest.raises(MarketDataApplicationError) as missing:
        _fill(sessions, world, uuid4())
    assert missing.value.code == "gap_not_found"

    with sessions() as db:
        assert db.scalar(select(BackfillJob).limit(1)) is None  # nothing was queued


def test_another_workspace_cannot_fill_this_workspaces_gap(sessions, world) -> None:
    with sessions() as db, pytest.raises(MarketDataApplicationError) as refused:
        market_data_application.enqueue_gap_backfill(
            db, world["stranger"], world["open_gap"], lambda *_: None
        )

    assert refused.value.code == "instrument_unavailable"
