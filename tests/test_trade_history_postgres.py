"""The trade history against a real PostgreSQL, fed by the real order flow
(app/trading/application/trade_history.py): fills with their order, bot, reason and realized
profit, the totals, the filters, keyset paging, and positions. Opt-in like the other postgres
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
from app.trading.application import account_funding, order_flow, trade_history
from app.trading.application.paper_provisioning import create_approved_bot
from app.trading.application.trade_history import FillFilters, order_reason
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
        connection.execute(
            text("TRUNCATE fx.workspace, fx.instrument, fx.outbox_event, fx.system_event CASCADE")
        )
    _migrate(engine, "base", downgrade=True)
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.commit()
    engine.dispose()


def _instrument(db, exchange, market, base):
    instrument = Instrument(
        exchange_id=exchange.id,
        market_id=market.id,
        symbol=f"{base}{uuid4().hex[:4]}",
        base_asset=base,
        quote_asset="USDT",
        price_scale=2,
        quantity_scale=4,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.0001"),
    )
    db.add(instrument)
    db.flush()
    db.add(
        Candle(
            instrument_id=instrument.id,
            timeframe="1m",
            open_time=datetime.now(UTC) - timedelta(minutes=1),
            close_time=datetime.now(UTC),
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            source="test",
            is_final=True,
        )
    )
    return instrument


@pytest.fixture
def world(sessions):
    """A workspace with two Binance instruments (BTC, ETH), each with a funded account and a bot,
    and another workspace with a trade of its own."""
    with sessions() as db:
        exchange = db.scalar(select(Exchange).where(Exchange.code == "binance"))
        market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
        workspaces = [Workspace(name=f"history-{uuid4().hex[:6]}"), Workspace(name="other")]
        db.add_all(workspaces)
        db.flush()
        ids = {}
        for workspace, label in zip(workspaces, ("mine", "other"), strict=True):
            connection = ExchangeConnection(
                workspace_id=workspace.id,
                exchange_id=exchange.id,
                label=label,
                environment="testnet",
                api_base_url="https://example.invalid",
            )
            db.add(connection)
            db.flush()
            for base in ("BTC", "ETH") if label == "mine" else ("BTC",):
                instrument = _instrument(db, exchange, market, base)
                account = TradingAccount(
                    workspace_id=workspace.id,
                    connection_id=connection.id,
                    mode="paper",
                    base_currency="USDT",
                )
                db.add(account)
                db.flush()
                account_funding.seed_paper_deposit(db, account, amount=Decimal(10000), asset="USDT")
                db.commit()
                bot = create_approved_bot(
                    db, workspace.id, account, instrument, bot_name=f"{label}-{base}-bot"
                )
                ids[(label, base)] = (workspace.id, account.id, instrument.id, bot.id)
        return ids


def _trade(sessions, ids, key, side, quantity, price, client_prefix="dummy-"):
    workspace_id, account_id, instrument_id, _ = ids[key]
    with sessions() as db:
        candle = db.scalar(select(Candle).where(Candle.instrument_id == instrument_id))
        candle.open = candle.high = candle.low = candle.close = Decimal(price)
        db.commit()
        order_flow.place_order(
            db,
            order_flow.PlaceOrderCommand(
                workspace_id=workspace_id,
                account_id=account_id,
                instrument_id=instrument_id,
                side=side,
                order_type="market",
                quantity=Decimal(quantity),
                client_order_id=f"{client_prefix}{uuid4().hex[:10]}",
            ),
        )


def _fills(sessions, workspace_id, filters=None, **kwargs):
    with sessions() as db:
        return trade_history.list_fills(
            db, workspace_id, filters or FillFilters(), limit=kwargs.pop("limit", 50), **kwargs
        )


def test_a_buy_and_a_stop_loss_exit_show_their_reason_bot_and_realized_loss(
    sessions, world
) -> None:
    mine = world[("mine", "BTC")][0]
    _trade(sessions, world, ("mine", "BTC"), "buy", "2", 100)
    _trade(sessions, world, ("mine", "BTC"), "sell", "2", 90, client_prefix="stop-")

    page = _fills(sessions, mine, FillFilters(instrument_id=world[("mine", "BTC")][2]))

    sell, buy = page.items
    assert (sell.order.side, sell.reason, sell.bot_name) == ("sell", "stop_loss", "mine-BTC-bot")
    assert (buy.order.side, buy.reason) == ("buy", "signal")
    assert sell.realized_pnl == Decimal(-20) and buy.realized_pnl == 0
    assert sell.fill.price * sell.fill.quantity == Decimal(180)
    assert sell.quote_asset == "USDT" and sell.symbol.startswith("BTC")


def test_totals_cover_every_matching_fill_not_just_the_page(sessions, world) -> None:
    mine = world[("mine", "BTC")][0]
    _trade(sessions, world, ("mine", "BTC"), "buy", "2", 100)
    _trade(sessions, world, ("mine", "BTC"), "sell", "2", 110)
    _trade(sessions, world, ("mine", "ETH"), "buy", "1", 100)

    page = _fills(sessions, mine, limit=1)

    assert len(page.items) == 1 and page.next_before is not None
    (total,) = page.totals
    assert total.quote_asset == "USDT" and total.fills == 3
    assert total.realized_pnl == Decimal(20)
    assert total.fees == Decimal("0.2") + Decimal("0.22") + Decimal("0.1")  # 0.1% of each notional


def test_the_filters_narrow_the_list_and_the_totals(sessions, world) -> None:
    mine = world[("mine", "BTC")][0]
    btc_bot = world[("mine", "BTC")][3]
    _trade(sessions, world, ("mine", "BTC"), "buy", "2", 100)
    _trade(sessions, world, ("mine", "BTC"), "sell", "2", 110)
    _trade(sessions, world, ("mine", "ETH"), "buy", "1", 100)

    by_bot = _fills(sessions, mine, FillFilters(bot_id=btc_bot))
    sells = _fills(sessions, mine, FillFilters(side="sell"))
    future = _fills(sessions, mine, FillFilters(from_time=datetime.now(UTC) + timedelta(days=1)))
    past = _fills(sessions, mine, FillFilters(to_time=datetime.now(UTC) + timedelta(days=1)))

    assert {row.symbol[:3] for row in by_bot.items} == {"BTC"} and len(by_bot.items) == 2
    assert by_bot.totals[0].fills == 2
    assert [row.order.side for row in sells.items] == ["sell"] and sells.totals[0].fills == 1
    assert future.items == [] and future.totals == []
    assert len(past.items) == 3
    nobody = _fills(sessions, mine, FillFilters(bot_id=uuid4()))
    assert nobody.items == []


def test_paging_follows_the_keyset_without_repeating_or_missing(sessions, world) -> None:
    mine = world[("mine", "BTC")][0]
    for _ in range(3):
        _trade(sessions, world, ("mine", "BTC"), "buy", "1", 100)
        _trade(sessions, world, ("mine", "BTC"), "sell", "1", 100)

    seen, before = [], None
    while True:
        page = _fills(sessions, mine, limit=4, before=before)
        seen += [row.fill.id for row in page.items]
        if page.next_before is None:
            break
        before = page.next_before

    assert len(seen) == 6 and len(set(seen)) == 6


def test_a_workspace_sees_only_its_own_fills(sessions, world) -> None:
    _trade(sessions, world, ("mine", "BTC"), "buy", "1", 100)
    _trade(sessions, world, ("other", "BTC"), "buy", "3", 100)

    mine = _fills(sessions, world[("mine", "BTC")][0])
    other = _fills(sessions, world[("other", "BTC")][0])

    assert [row.fill.quantity for row in mine.items] == [Decimal(1)]
    assert [row.fill.quantity for row in other.items] == [Decimal(3)]


def test_the_reason_comes_from_how_the_order_was_named() -> None:
    assert order_reason("dummy-ab12") == "signal"
    assert order_reason("stop-ab12") == "stop_loss"
    assert order_reason("dote-close-ab12") == "reversal_close"
    assert order_reason("emergency-close-ab12") == "emergency_close"
    assert order_reason("my-own-order") == "manual"
    assert order_reason("") == "manual"


def test_positions_list_open_ones_first_then_closed_with_their_result(sessions, world) -> None:
    mine = world[("mine", "BTC")][0]
    _trade(sessions, world, ("mine", "BTC"), "buy", "2", 100)
    _trade(sessions, world, ("mine", "BTC"), "sell", "2", 110)
    _trade(sessions, world, ("mine", "ETH"), "buy", "1", 100)

    with sessions() as db:
        total, rows = trade_history.list_positions(
            db, mine, statuses=[], bot_id=None, instrument_id=None, limit=50
        )
        _, closed = trade_history.list_positions(
            db, mine, statuses=["closed"], bot_id=None, instrument_id=None, limit=50
        )
        _, by_bot = trade_history.list_positions(
            db, mine, statuses=[], bot_id=world[("mine", "ETH")][3], instrument_id=None, limit=50
        )

    assert total == 2
    assert [row.position.status for row in rows] == ["open", "closed"]
    assert rows[0].bot_name == "mine-ETH-bot" and rows[0].position.quantity == Decimal(1)
    assert [row.position.realized_pnl for row in closed] == [Decimal(20)]
    assert closed[0].position.quantity == 0 and closed[0].position.closed_at is not None
    assert [row.bot_name for row in by_bot] == ["mine-ETH-bot"]
