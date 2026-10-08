"""The bot overview against a real PostgreSQL, fed by the real order flow: a funded paper account
with a bot, a trade, a mark price, and an account nobody uses. Opt-in like the other postgres
tests: needs an EMPTY dedicated worker_test_* database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.connections import Exchange, ExchangeConnection, Market
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.trading import TradingAccount
from app.models.workspace import Workspace
from app.trading.application import account_funding, bot_overview, order_flow
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
        # The migrations' downgrade refuses a table that holds paper orders; the seeded
        # catalog (exchanges, markets) is theirs to remove, so only this test's rows go.
        connection.execute(
            text("TRUNCATE fx.workspace, fx.instrument, fx.outbox_event, fx.system_event CASCADE")
        )
    _migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.commit()
    engine.dispose()


@pytest.fixture
def world(sessions):
    """A workspace with a Binance instrument priced at 100, a funded account with a bot on it,
    and a second account nothing uses."""
    with sessions() as db:
        workspace = Workspace(name=f"overview-{uuid4().hex[:6]}")
        exchange = db.scalar(select(Exchange).where(Exchange.code == "binance"))
        market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
        db.add(workspace)
        db.flush()
        connection = ExchangeConnection(
            workspace_id=workspace.id,
            exchange_id=exchange.id,
            label="overview",
            environment="testnet",
            api_base_url="https://example.invalid",
        )
        instrument = Instrument(
            exchange_id=exchange.id,
            market_id=market.id,
            symbol=f"B{uuid4().hex[:5]}",
            base_asset="BTC",
            quote_asset="USDT",
            price_scale=2,
            quantity_scale=4,
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.0001"),
        )
        db.add_all([connection, instrument])
        db.flush()
        used = TradingAccount(
            workspace_id=workspace.id,
            connection_id=connection.id,
            mode="paper",
            base_currency="USDT",
        )
        unused = TradingAccount(
            workspace_id=workspace.id,
            connection_id=connection.id,
            mode="paper",
            base_currency="USDT",
        )
        db.add_all([used, unused])
        now = datetime.now(UTC)
        db.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="1m",
                open_time=now - timedelta(minutes=1),
                close_time=now,
                open=Decimal(100),
                high=Decimal(100),
                low=Decimal(100),
                close=Decimal(100),
                source="test",
                is_final=True,
            )
        )
        db.flush()
        account_funding.seed_paper_deposit(db, used, amount=Decimal(1000), asset="USDT")
        db.commit()
        create_approved_bot(db, workspace.id, used, instrument, bot_name="overview-bot")
        return workspace.id, used.id, unused.id, instrument.id


def _set_price(sessions, instrument_id, price):
    with sessions() as db:
        candle = db.scalar(select(Candle).where(Candle.instrument_id == instrument_id))
        candle.open = candle.high = candle.low = candle.close = Decimal(price)
        db.commit()


def _trade(sessions, world, side, quantity, price):
    workspace_id, account_id, _, instrument_id = world
    _set_price(sessions, instrument_id, price)
    with sessions() as db:
        order_flow.place_order(
            db,
            order_flow.PlaceOrderCommand(
                workspace_id=workspace_id,
                account_id=account_id,
                instrument_id=instrument_id,
                side=side,
                order_type="market",
                quantity=Decimal(quantity),
                client_order_id=f"c-{uuid4().hex[:10]}",
            ),
        )


def _overview(sessions, workspace_id):
    with sessions() as db:
        return bot_overview.build_overview(db, workspace_id)


def test_a_funded_bot_with_no_trades_stands_at_its_deposit(sessions, world) -> None:
    workspace_id = world[0]

    (bot,), _ = _overview(sessions, workspace_id)

    assert (bot.deposits, bot.cash, bot.equity) == (Decimal(1000), Decimal(1000), Decimal(1000))
    assert bot.return_pct == 0 and bot.position is None
    assert (bot.realized_pnl, bot.fees_paid, bot.closed_trades) == (0, 0, 0)
    assert bot.exchange_code == "binance" and bot.quote_asset == "USDT"


def test_an_open_position_is_valued_at_the_latest_close(sessions, world) -> None:
    workspace_id, _, _, instrument_id = world
    _trade(sessions, world, "buy", "2", 100)  # 200 USDT + 0.2 fee
    _set_price(sessions, instrument_id, 110)

    (bot,), _ = _overview(sessions, workspace_id)

    assert bot.position is not None
    assert (bot.position.side, bot.position.quantity) == ("long", Decimal(2))
    assert bot.position.mark_price == Decimal(110)
    assert bot.position.unrealized_pnl == Decimal(20)
    assert bot.cash == Decimal("799.8")  # 1000 - 200 - 0.2 fee
    assert bot.equity == Decimal("1019.8")  # cash + 2 x 110
    assert bot.fees_paid == Decimal("0.2")
    assert bot.return_pct == Decimal("1.98")


def test_a_closed_trade_shows_its_realized_profit_and_fees(sessions, world) -> None:
    workspace_id = world[0]
    _trade(sessions, world, "buy", "2", 100)
    _trade(sessions, world, "sell", "2", 110)

    (bot,), _ = _overview(sessions, workspace_id)

    assert bot.position is None
    assert bot.realized_pnl == Decimal(20)
    assert bot.closed_trades == 1
    assert bot.fees_paid == Decimal("0.42")  # 0.2 on the buy, 0.22 on the sell
    assert bot.equity == Decimal("1019.58")


def test_accounts_are_told_apart_by_their_bots_and_balances(sessions, world) -> None:
    workspace_id, used_id, unused_id, _ = world

    _, accounts = _overview(sessions, workspace_id)

    by_id = {item.account.id: item for item in accounts}
    assert by_id[used_id].bot_names == ["overview-bot"]
    assert by_id[used_id].balances == {"USDT": Decimal(1000)}
    assert by_id[unused_id].bot_names == [] and by_id[unused_id].balances == {}


def test_a_workspace_with_nothing_has_an_empty_overview(sessions) -> None:
    with sessions() as db:
        workspace = Workspace(name="empty-overview")
        db.add(workspace)
        db.commit()

    assert _overview(sessions, workspace.id) == ([], [])
