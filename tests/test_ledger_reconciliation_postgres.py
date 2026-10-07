"""The ledger reconciliation against a real PostgreSQL, fed by the real writer: orders placed
through `order_flow.place_order` (buy, add, reduce, flip, close) must reconcile with nothing to
report, and each kind of damage done afterwards must be found. Opt-in like the other postgres
tests: needs an EMPTY dedicated worker_test_* database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import OutboxEvent
from app.models.connections import Exchange, ExchangeConnection, Market
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import TradingHalt
from app.models.trading import TradingAccount
from app.models.workspace import Workspace
from app.trading.application import ledger_check, ledger_reconciliation, order_flow
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
    """A workspace with an OANDA instrument priced at 150 and a paper account to trade on;
    each test gets its own account so that one test's damage does not reach another's."""
    with sessions() as db:
        workspace = Workspace(name=f"recon-{uuid4().hex[:6]}")
        exchange = db.scalar(select(Exchange).where(Exchange.code == "oanda"))
        market = db.scalar(select(Market).limit(1))
        db.add(workspace)
        db.flush()
        connection = ExchangeConnection(
            workspace_id=workspace.id,
            exchange_id=exchange.id,
            label="recon",
            environment="practice",
            api_base_url="https://example.invalid",
        )
        instrument = Instrument(
            exchange_id=exchange.id,
            market_id=market.id,
            symbol=f"R{uuid4().hex[:5]}",
            base_asset="USD",
            quote_asset="JPY",
            price_scale=3,
            quantity_scale=0,
            tick_size=Decimal("0.001"),
            step_size=Decimal("1"),
        )
        db.add_all([connection, instrument])
        db.flush()
        account = TradingAccount(
            workspace_id=workspace.id,
            connection_id=connection.id,
            mode="paper",
            base_currency="JPY",
        )
        now = datetime.now(UTC)
        db.add(account)
        db.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="1m",
                open_time=now - timedelta(minutes=1),
                close_time=now,
                open=Decimal(150),
                high=Decimal(150),
                low=Decimal(150),
                close=Decimal(150),
                source="test",
                is_final=True,
            )
        )
        db.commit()
        return workspace.id, account.id, instrument.id


def _trade(sessions, world, side, quantity, price=None):
    workspace_id, account_id, instrument_id = world
    with sessions() as db:
        if price is not None:
            candle = db.scalar(select(Candle).where(Candle.instrument_id == instrument_id).limit(1))
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
                client_order_id=f"c-{uuid4().hex[:10]}",
            ),
        )


def _codes(sessions, account_id):
    with sessions() as db:
        return sorted({f.code for f in ledger_reconciliation.reconcile_account(db, account_id)})


def _sql(sessions, statement, **params):
    with sessions() as db:
        db.execute(text(statement), params)
        db.commit()


def test_a_real_run_of_trades_reconciles_with_nothing_to_report(sessions, world) -> None:
    _, account_id, _ = world

    assert _codes(sessions, account_id) == []  # nothing traded yet
    _trade(sessions, world, "buy", 10, price=150)
    assert _codes(sessions, account_id) == []
    _trade(sessions, world, "buy", 5, price=152)  # adds: the average moves
    _trade(sessions, world, "sell", 4, price=155)  # reduces: realizes a profit
    assert _codes(sessions, account_id) == []
    _trade(sessions, world, "sell", 20, price=149)  # flips to a short of 9
    assert _codes(sessions, account_id) == []
    _trade(sessions, world, "sell", 3, price=148)  # adds to the short
    _trade(sessions, world, "buy", 12, price=146)  # closes it exactly
    assert _codes(sessions, account_id) == []


def test_a_wrong_ledger_amount_is_found(sessions, world) -> None:
    _, account_id, _ = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "UPDATE fx.ledger_entry SET amount = amount + 5 "
        "WHERE account_id = :a AND entry_type = 'cash'",
        a=account_id,
    )

    assert "ledger_cash_mismatch" in _codes(sessions, account_id)


def test_a_missing_ledger_transaction_is_found(sessions, world) -> None:
    _, account_id, _ = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "DELETE FROM fx.ledger_entry WHERE account_id = :a",
        a=account_id,
    )
    _sql(
        sessions,
        "DELETE FROM fx.ledger_transaction WHERE account_id = :a",
        a=account_id,
    )

    assert "fill_without_ledger" in _codes(sessions, account_id)


def test_a_position_that_does_not_match_the_fills_is_found(sessions, world) -> None:
    _, account_id, _ = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "UPDATE fx.trading_position SET quantity = 7 WHERE account_id = :a",
        a=account_id,
    )

    assert "position_quantity_mismatch" in _codes(sessions, account_id)


def test_a_wrong_average_entry_price_is_found(sessions, world) -> None:
    _, account_id, _ = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "UPDATE fx.trading_position SET average_entry_price = 140 WHERE account_id = :a",
        a=account_id,
    )

    assert _codes(sessions, account_id) == ["position_cost_basis_mismatch"]


def test_an_order_whose_filled_quantity_is_wrong_is_found(sessions, world) -> None:
    _, account_id, _ = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "UPDATE fx.trade_order SET filled_quantity = 4 WHERE account_id = :a",
        a=account_id,
    )

    assert "order_filled_quantity_mismatch" in _codes(sessions, account_id)


def test_an_order_status_the_history_never_recorded_is_found(sessions, world) -> None:
    _, account_id, _ = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "UPDATE fx.trade_order SET status = 'cancelled' WHERE account_id = :a",
        a=account_id,
    )

    assert "order_status_not_in_history" in _codes(sessions, account_id)


def test_a_mismatch_halts_the_account_once_and_announces_it(sessions, world) -> None:
    workspace_id, account_id, instrument_id = world
    _trade(sessions, world, "buy", 10, price=150)
    _sql(
        sessions,
        "UPDATE fx.trading_position SET quantity = 7 WHERE account_id = :a",
        a=account_id,
    )

    with sessions() as db:
        assert ledger_check.check_ledger_reconciliation(db) >= 1
    with sessions() as db:
        assert ledger_check.check_ledger_reconciliation(db) == 0  # already halted, not re-announced
        halts = db.scalars(
            select(TradingHalt).where(
                TradingHalt.scope_id == account_id, TradingHalt.status == "active"
            )
        ).all()
        (halt,) = halts
        assert (halt.reason_code, halt.level, halt.scope_type) == (
            "ledger_mismatch",
            "emergency_stopped",
            "account",
        )
        assert halt.workspace_id == workspace_id and halt.auto_releasable is False
        outbox = db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == halt.id)).all()
        assert len(outbox) == 1

        # No new entry on the halted account; closing is still allowed (see the plan).
        with pytest.raises(order_flow.OrderFlowError) as blocked:
            order_flow.place_order(
                db,
                order_flow.PlaceOrderCommand(
                    workspace_id=workspace_id,
                    account_id=account_id,
                    instrument_id=instrument_id,
                    side="buy",
                    order_type="market",
                    quantity=Decimal(1),
                    client_order_id=f"c-{uuid4().hex[:10]}",
                ),
            )
        assert blocked.value.code == "trading_halted"
