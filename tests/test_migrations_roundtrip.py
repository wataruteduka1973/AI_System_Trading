"""Opt-in integration test; requires an EMPTY dedicated worker_test_* database.

Verifies that the full Alembic migration chain (base -> head -> base -> head -> base)
runs cleanly against a real PostgreSQL database. This does not check per-migration data
preservation (see test_worker_leases_postgres.py for the 0004<->0005 data round-trip);
it only checks that every upgrade/downgrade step in the chain executes without error and
that "downgrade to base" actually leaves the database empty (no leftover objects).

Runs first in the worker-storage CI job (before the other worker_test_* files) so it
starts from, and returns, a genuinely empty database -- the same precondition the other
files in that job already rely on.
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_URL = os.environ.get("WORKER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="Requires dedicated WORKER_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


def migrate(engine, revision, downgrade=False):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def non_system_table_count(connection) -> int:
    return connection.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
        )
    )


@pytest.fixture(scope="module")
def engine():
    url = make_url(TEST_URL)
    if not (url.database or "").startswith("worker_test_"):
        pytest.fail("Refusing non-test database; name must start with worker_test_")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        if non_system_table_count(connection):
            pytest.fail("Refusing a non-empty test database; use a fresh worker_test_* database")
    try:
        yield engine
    finally:
        engine.dispose()


def test_full_chain_upgrade_downgrade_roundtrip(engine):
    # base -> head: every migration's upgrade() must apply cleanly to an empty database.
    migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'fx'")
        ) == 1
        assert non_system_table_count(connection) > 0

    # head -> base: every migration's downgrade() must apply cleanly, ending fully empty.
    migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'fx'")
            )
            == 0
        )
        assert non_system_table_count(connection) == 0

    # Re-running upgrade after a full downgrade must also work (not just the first time).
    migrate(engine, "head")
    with engine.connect() as connection:
        assert non_system_table_count(connection) > 0

    # Leave the database empty for the next test file in this CI job.
    migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        assert non_system_table_count(connection) == 0
