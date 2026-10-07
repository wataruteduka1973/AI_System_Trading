"""`fx.instrument_spread_history` against a real PostgreSQL (migration 20261007_0011 and the
sampled append in `record_spread_observation`). Opt-in like the other postgres tests: needs
an EMPTY dedicated worker_test_* database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.market_data.application.spread_tracking import record_spread_observation
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL"),
]
ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)


def migrate(engine, revision, downgrade=False):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


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
    migrate(engine, "head")
    try:
        yield engine
    finally:
        migrate(engine, "base", downgrade=True)
        with engine.connect() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            connection.commit()
        engine.dispose()


@pytest.fixture
def instrument_id(engine):
    instrument = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO fx.instrument(id,exchange_id,market_id,symbol,base_asset,quote_asset,
              price_scale,quantity_scale,tick_size,step_size)
            SELECT :id,e.id,m.id,CAST(:id AS text),'BTC','USDT',2,4,0.01,0.0001
            FROM fx.exchange e CROSS JOIN fx.market m
            WHERE e.code='binance_public' AND m.code='crypto_spot'
            """),
            {"id": instrument},
        )
    return instrument


def _record(engine, instrument_id, at, bid="100", ask="101"):
    with sessionmaker(engine)() as db:
        record_spread_observation(
            db, instrument_id, bid=Decimal(bid), ask=Decimal(ask), observed_at=at, source="test"
        )


def _history(engine, instrument_id):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT observed_at, bid, ask FROM fx.instrument_spread_history "
                "WHERE instrument_id = :id ORDER BY observed_at"
            ),
            {"id": instrument_id},
        ).all()


def test_observations_inside_the_window_are_not_stored_again(engine, instrument_id):
    _record(engine, instrument_id, START)
    _record(engine, instrument_id, START + timedelta(seconds=10), ask="102")
    _record(engine, instrument_id, START + timedelta(seconds=29), ask="103")
    _record(engine, instrument_id, START + timedelta(seconds=30), ask="104")
    _record(engine, instrument_id, START + timedelta(seconds=75), ask="105")

    rows = _history(engine, instrument_id)

    assert [r.observed_at for r in rows] == [
        START,
        START + timedelta(seconds=30),
        START + timedelta(seconds=75),
    ]
    assert [r.ask for r in rows] == [Decimal(101), Decimal(104), Decimal(105)]


def test_the_latest_value_still_follows_every_observation(engine, instrument_id):
    _record(engine, instrument_id, START)
    _record(engine, instrument_id, START + timedelta(seconds=5), bid="100.5", ask="101.5")

    with engine.connect() as connection:
        latest = connection.execute(
            text("SELECT bid, ask FROM fx.instrument_spread WHERE instrument_id = :id"),
            {"id": instrument_id},
        ).one()

    assert (latest.bid, latest.ask) == (Decimal("100.5"), Decimal("101.5"))
    assert len(_history(engine, instrument_id)) == 1


def test_a_replayed_observation_is_not_an_error(engine, instrument_id):
    _record(engine, instrument_id, START)
    _record(engine, instrument_id, START)

    assert len(_history(engine, instrument_id)) == 1


def test_a_missing_history_table_does_not_break_the_latest_value(engine, instrument_id):
    migrate(engine, "20261002_0010", downgrade=True)
    try:
        _record(engine, instrument_id, START)
        with engine.connect() as connection:
            stored = connection.scalar(
                text("SELECT count(*) FROM fx.instrument_spread WHERE instrument_id = :id"),
                {"id": instrument_id},
            )
        assert stored == 1
    finally:
        migrate(engine, "head")
