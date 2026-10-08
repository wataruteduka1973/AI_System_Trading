"""The event log's queries against a real PostgreSQL (app/monitoring/event_log.py): every filter,
the bot match through the target and through the payload, keyset paging across events that share a
timestamp, a LIKE search that treats % and _ as plain characters, and one workspace never seeing
another's events. Opt-in like the other postgres tests: needs an EMPTY dedicated worker_test_*
database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import SystemEvent
from app.models.connections import Exchange, ExchangeConnection, Market
from app.models.instruments import Instrument
from app.models.trading import TradingAccount
from app.models.workspace import Workspace
from app.monitoring import event_log
from app.monitoring.event_log import EventFilters
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
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


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
        connection.execute(text("TRUNCATE fx.workspace, fx.outbox_event, fx.system_event CASCADE"))
    _migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.commit()
    engine.dispose()


def _event(workspace_id, minutes_ago=0, **overrides):
    values = dict(
        workspace_id=workspace_id,
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        severity="error",
        category="system",
        event_type="trading_worker_stalled",
        reason_code="worker_stalled",
        source_type="watchdog",
        correlation_id=uuid4(),
        message="トレーディングWorkerが止まっている可能性があります",
        payload={},
    )
    values.update(overrides)
    return SystemEvent(**values)


@pytest.fixture
def workspace(sessions):
    with sessions() as db:
        item = Workspace(name=f"events-{uuid4().hex[:6]}")
        db.add(item)
        db.commit()
        return item.id


def _query(sessions, workspace_id, filters=None, **kwargs):
    with sessions() as db:
        return event_log.query_events(
            db, workspace_id, filters or EventFilters(), limit=kwargs.pop("limit", 50), **kwargs
        )


def test_events_are_newest_first_and_only_this_workspaces(sessions, workspace) -> None:
    with sessions() as db:
        other = Workspace(name="other")
        db.add(other)
        db.flush()
        db.add_all(
            [
                _event(workspace, 30, message="old"),
                _event(workspace, 5, message="new"),
                _event(other.id, 1, message="somebody else's"),
            ]
        )
        db.commit()

    page = _query(sessions, workspace)

    assert [event.message for event in page.items] == ["new", "old"]
    assert page.next_before is None


def test_each_filter_narrows_the_log(sessions, workspace) -> None:
    correlation = uuid4()
    with sessions() as db:
        db.add_all(
            [
                _event(
                    workspace,
                    1,
                    severity="critical",
                    category="risk",
                    event_type="halt",
                    reason_code="ledger_mismatch",
                    correlation_id=correlation,
                ),
                _event(
                    workspace,
                    2,
                    severity="warning",
                    category="risk",
                    event_type="halt",
                    reason_code="data_delay",
                ),
                _event(
                    workspace,
                    3,
                    severity="error",
                    category="market_data",
                    event_type="stalled",
                    reason_code=None,
                ),
                _event(
                    workspace,
                    120,
                    severity="error",
                    category="market_data",
                    event_type="stalled",
                    reason_code=None,
                ),
            ]
        )
        db.commit()

    def count(**filters):
        return len(_query(sessions, workspace, EventFilters(**filters)).items)

    assert count(severities=["critical", "warning"]) == 2
    assert count(category="risk") == 2
    assert count(event_type="stalled") == 2
    assert count(reason_code="ledger_mismatch") == 1
    assert count(correlation_id=correlation) == 1
    assert count(from_time=NOW - timedelta(minutes=10)) == 3
    assert count(to_time=NOW - timedelta(minutes=10)) == 1
    assert count(category="risk", severities=["critical"]) == 1  # filters add up


def test_the_bot_filter_matches_the_target_and_the_payload(sessions, workspace) -> None:
    bot, other_bot = uuid4(), uuid4()
    with sessions() as db:
        db.add_all(
            [
                _event(workspace, 1, message="about the bot", target_type="bot", target_id=bot),
                _event(
                    workspace,
                    2,
                    message="named in the payload",
                    target_type="account",
                    target_id=uuid4(),
                    payload={"bot_id": str(bot)},
                ),
                _event(workspace, 3, message="another bot", target_type="bot", target_id=other_bot),
                _event(workspace, 4, message="unrelated"),
            ]
        )
        db.commit()

    page = _query(sessions, workspace, EventFilters(bot_id=bot))

    assert [event.message for event in page.items] == ["about the bot", "named in the payload"]


def test_paging_follows_the_keyset_even_when_events_share_a_time(sessions, workspace) -> None:
    with sessions() as db:
        db.add_all([_event(workspace, 0, message=f"same-time-{i}") for i in range(5)])
        db.add_all([_event(workspace, 10 + i, message=f"older-{i}") for i in range(3)])
        db.commit()

    seen: list[str] = []
    before = None
    pages = 0
    while True:
        page = _query(sessions, workspace, limit=3, before=before)
        seen += [event.message for event in page.items]
        pages += 1
        if page.next_before is None:
            break
        before = page.next_before

    assert pages == 3
    assert len(seen) == 8 and len(set(seen)) == 8  # nothing twice, nothing missed
    assert seen[-1] == "older-2"


def test_a_search_word_is_matched_literally(sessions, workspace) -> None:
    with sessions() as db:
        db.add_all(
            [
                _event(workspace, 1, message="遅れが50%を超えました"),
                _event(workspace, 2, message="遅れが50ポイントを超えました"),
                _event(workspace, 3, message="a_b"),
                _event(workspace, 4, message="axb"),
            ]
        )
        db.commit()

    def found(word):
        return [
            event.message for event in _query(sessions, workspace, EventFilters(text=word)).items
        ]

    assert found("50%") == ["遅れが50%を超えました"]  # % is not a wildcard
    assert found("a_b") == ["a_b"]  # nor is _
    assert len(found("遅れ")) == 2


def test_the_page_size_is_capped(sessions, workspace) -> None:
    with sessions() as db:
        db.add_all([_event(workspace, i) for i in range(3)])
        db.commit()

    assert len(_query(sessions, workspace, limit=10_000).items) == 3
    assert len(_query(sessions, workspace, limit=0).items) == 1  # at least one


def test_the_facets_list_what_the_workspace_has(sessions, workspace) -> None:
    with sessions() as db:
        other = Workspace(name="facets-other")
        db.add(other)
        db.flush()
        db.add_all(
            [
                _event(
                    workspace,
                    1,
                    severity="critical",
                    category="risk",
                    event_type="halt",
                    reason_code="ledger_mismatch",
                ),
                _event(
                    workspace,
                    2,
                    severity="error",
                    category="system",
                    event_type="stalled",
                    reason_code=None,
                ),
                _event(
                    other.id,
                    3,
                    severity="info",
                    category="notification",
                    event_type="other",
                    reason_code="x",
                ),
            ]
        )
        db.commit()

    with sessions() as db:
        facets = event_log.event_facets(db, workspace)

    assert facets.severities == ["critical", "error"]
    assert facets.categories == ["risk", "system"]
    assert facets.event_types == ["halt", "stalled"]
    assert facets.reason_codes == ["ledger_mismatch"]


def _real_bot(sessions, workspace_id, name):
    """A bot that exists (the filter looks its name up)."""
    with sessions() as db:
        exchange = db.scalar(select(Exchange).where(Exchange.code == "binance"))
        market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
        connection = ExchangeConnection(
            workspace_id=workspace_id,
            exchange_id=exchange.id,
            label="events",
            environment="testnet",
            api_base_url="https://example.invalid",
        )
        instrument = Instrument(
            exchange_id=exchange.id,
            market_id=market.id,
            symbol=f"E{uuid4().hex[:5]}",
            base_asset="BTC",
            quote_asset="USDT",
            price_scale=2,
            quantity_scale=4,
            tick_size=1,
            step_size=1,
        )
        db.add_all([connection, instrument])
        db.flush()
        account = TradingAccount(
            workspace_id=workspace_id,
            connection_id=connection.id,
            mode="paper",
            base_currency="USDT",
        )
        db.add(account)
        db.commit()
        return create_approved_bot(db, workspace_id, account, instrument, bot_name=name).id


def test_the_bot_filter_also_finds_the_stalled_worker_alert_that_lists_the_bot_by_name(
    sessions, workspace
) -> None:
    bot = _real_bot(sessions, workspace, "btc-4h-listed")
    with sessions() as db:
        db.add_all(
            [
                _event(
                    workspace,
                    1,
                    message="stalled, names the bot",
                    payload={"bot_names": ["x", "btc-4h-listed"]},
                ),
                _event(
                    workspace, 2, message="stalled, other bots", payload={"bot_names": ["x", "y"]}
                ),
            ]
        )
        db.commit()

    page = _query(sessions, workspace, EventFilters(bot_id=bot))

    assert [event.message for event in page.items] == ["stalled, names the bot"]
