"""The decision history against a real PostgreSQL (app/trading/application/decision_history.py):
what became of each signal, the summary, the filters, keyset paging, the failed risk checks, the
order behind an entry, and the halts that overlap. Opt-in like the other postgres tests: needs an
EMPTY dedicated worker_test_* database, and leaves it empty."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.models.audit import AuditLog
from app.models.connections import Exchange, ExchangeConnection, Market
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import BotRun, RiskDecision, Signal, TradingHalt
from app.models.trading import TradingAccount
from app.models.workspace import Workspace
from app.trading.application import account_funding, decision_history, order_flow
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
BASE = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


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


@pytest.fixture
def world(sessions):
    """A workspace with a funded account and a bot (plus a second bot on another account, whose
    signals and halts must not leak in), and a run for each."""
    with sessions() as db:
        exchange = db.scalar(select(Exchange).where(Exchange.code == "binance"))
        market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
        workspace = Workspace(name=f"decisions-{uuid4().hex[:6]}")
        db.add(workspace)
        db.flush()
        connection = ExchangeConnection(
            workspace_id=workspace.id,
            exchange_id=exchange.id,
            label="decisions",
            environment="testnet",
            api_base_url="https://example.invalid",
        )
        db.add(connection)
        db.flush()
        made = {}
        for name in ("mine", "other"):
            instrument = Instrument(
                exchange_id=exchange.id,
                market_id=market.id,
                symbol=f"{name[:1].upper()}{uuid4().hex[:5]}",
                base_asset="BTC",
                quote_asset="USDT",
                price_scale=2,
                quantity_scale=4,
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.0001"),
            )
            db.add(instrument)
            db.flush()
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
            bot = create_approved_bot(db, workspace.id, account, instrument, bot_name=f"{name}-bot")
            run = BotRun(bot_id=bot.id, status="running", code_version="test")
            db.add(run)
            db.commit()
            made[name] = {"bot": bot, "run": run, "instrument": instrument, "account": account}
        made["workspace"] = workspace
        return made


def _bar(db, instrument, index, price=100):
    candle = Candle(
        instrument_id=instrument.id,
        timeframe="4h",
        open_time=BASE + timedelta(hours=4 * index),
        close_time=BASE + timedelta(hours=4 * index + 4),
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        source="test",
        is_final=True,
    )
    db.add(candle)
    db.flush()
    return candle


def _signal(db, who, index, action, *, minutes_after=0, **decision):
    """A signal for bar `index`; `decision` (outcome, reason_code, rule_results) adds the risk
    decision."""
    bot, run, instrument = who["bot"], who["run"], who["instrument"]
    candle = _bar(db, instrument, index)
    signal = Signal(
        workspace_id=bot.workspace_id,
        bot_run_id=run.id,
        candle_id=candle.id,
        strategy_version_id=bot.strategy_version_id,
        action=action,
        rationale={"kind": "donchian_breakout", "close": "100", "parameters": {"entry_period": 55}},
        input_checksum=uuid4().hex + uuid4().hex,
        created_at=candle.close_time + timedelta(minutes=minutes_after),
    )
    db.add(signal)
    db.flush()
    if decision:
        db.add(
            RiskDecision(
                signal_id=signal.id,
                risk_profile_version_id=bot.risk_profile_version_id,
                outcome=decision["outcome"],
                reason_code=decision.get("reason_code"),
                rule_results=decision.get("rule_results", {}),
                adjusted_quantity=decision.get("quantity"),
            )
        )
        db.flush()
    return signal


def _page(sessions, world, who="mine", **kwargs):
    with sessions() as db:
        bot = world[who]["bot"]
        defaults = dict(outcomes=[], from_time=None, to_time=None, limit=50)
        return decision_history.list_decisions(
            db, world["workspace"].id, bot, **{**defaults, **kwargs}
        )


def test_each_signal_becomes_its_outcome_with_the_checks_that_failed(sessions, world) -> None:
    mine = world["mine"]
    with sessions() as db:
        _signal(db, mine, 0, "hold")
        _signal(
            db,
            mine,
            1,
            "sell",
            outcome="deny",
            reason_code="hard_limit_breach",
            rule_results={
                "equity": {"passed": True, "value": "100"},
                "binance_no_short": {"passed": False},
                "per_trade_loss": {"passed": False, "pct": "0.02"},
            },
        )
        _signal(db, mine, 2, "buy", outcome="allow", quantity=Decimal("1"))  # allowed, no intent
        _signal(db, mine, 3, "buy")  # no decision at all
        db.commit()

    page = _page(sessions, world)

    by_action = {(e.signal.action, e.outcome): e for e in page.items}
    assert set(by_action) == {
        ("hold", "hold"),
        ("sell", "denied"),
        ("buy", "halted"),
        ("buy", "paused"),
    }
    denied = by_action[("sell", "denied")]
    assert denied.risk_reason_code == "hard_limit_breach"
    assert {c.name: c.detail for c in denied.failed_checks} == {
        "binance_no_short": {},
        "per_trade_loss": {"pct": "0.02"},
    }
    assert page.summary.evaluations == 4
    assert page.summary.counts == {
        "hold": 1,
        "ordered": 0,
        "closed": 0,
        "denied": 1,
        "halted": 1,
        "paused": 1,
    }


def test_an_entry_that_was_placed_shows_its_order_and_fill(sessions, world) -> None:
    mine = world["mine"]
    with sessions() as db:
        signal = _signal(db, mine, 0, "buy", outcome="allow", quantity=Decimal("2"))
        decision = db.scalar(select(RiskDecision).where(RiskDecision.signal_id == signal.id))
        db.commit()
        intent = order_flow.create_order_intent(
            db,
            order_flow.OrderIntentCommand(
                signal_id=signal.id,
                risk_decision_id=decision.id,
                side="buy",
                order_type="market",
                requested_quantity=Decimal(2),
            ),
        )
        order_flow.place_order(
            db,
            order_flow.PlaceOrderCommand(
                workspace_id=world["workspace"].id,
                account_id=mine["account"].id,
                instrument_id=mine["instrument"].id,
                side="buy",
                order_type="market",
                quantity=Decimal(2),
                client_order_id=f"dummy-{uuid4().hex[:8]}",
                order_intent_id=intent.id,
            ),
        )

    (entry,) = _page(sessions, world).items

    assert entry.outcome == "ordered" and entry.risk_outcome == "allow"
    assert entry.order_side == "buy" and entry.order_id is not None
    assert entry.fill_quantity == Decimal(2) and entry.fill_price is not None


def test_a_signal_that_closed_a_position_is_told_apart_from_a_paused_skip(sessions, world) -> None:
    mine = world["mine"]
    with sessions() as db:
        closing = _signal(db, mine, 0, "sell")
        _signal(db, mine, 1, "sell")  # nothing recorded: paused
        db.add(
            AuditLog(
                workspace_id=world["workspace"].id,
                action="dote_gate.close_only",
                resource_type="trading_position",
                resource_id=uuid4(),
                after_data={"signal_id": str(closing.id), "opposing_action": "sell"},
                correlation_id=uuid4(),
            )
        )
        db.commit()

    outcomes = [e.outcome for e in _page(sessions, world).items]

    assert sorted(outcomes) == ["closed", "paused"]


def test_the_outcome_filter_narrows_the_page_but_not_the_summary(sessions, world) -> None:
    mine = world["mine"]
    with sessions() as db:
        for index in range(3):
            _signal(db, mine, index, "hold")
        _signal(db, mine, 3, "sell", outcome="deny", reason_code="hard_limit_breach")
        db.commit()

    page = _page(sessions, world, outcomes=["denied"])

    assert [e.outcome for e in page.items] == ["denied"]
    assert page.summary.evaluations == 4 and page.summary.counts["hold"] == 3


def test_paging_follows_the_keyset_across_signals_of_the_same_moment(sessions, world) -> None:
    mine = world["mine"]
    with sessions() as db:
        for index in range(7):
            _signal(db, mine, index, "hold")
        db.commit()

    seen, before = [], None
    while True:
        page = _page(sessions, world, limit=3, before=before)
        seen += [e.signal.id for e in page.items]
        if page.next_before is None:
            break
        before = page.next_before

    assert len(seen) == 7 and len(set(seen)) == 7


def test_the_time_range_limits_the_page_and_the_summary(sessions, world) -> None:
    mine = world["mine"]
    with sessions() as db:
        for index in range(6):
            _signal(db, mine, index, "hold")
        db.commit()

    page = _page(
        sessions, world, from_time=BASE + timedelta(hours=12), to_time=BASE + timedelta(hours=20)
    )

    assert page.summary.evaluations == 2  # the bars that closed at 12:00+ and before 20:00
    assert len(page.items) == 2


def test_another_bots_signals_never_appear(sessions, world) -> None:
    with sessions() as db:
        _signal(db, world["mine"], 0, "hold")
        _signal(db, world["other"], 0, "sell", outcome="deny", reason_code="hard_limit_breach")
        db.commit()

    page = _page(sessions, world)

    assert [e.signal.action for e in page.items] == ["hold"]
    assert page.summary.evaluations == 1


def test_the_halts_that_apply_to_the_bot_are_listed_and_the_others_are_not(sessions, world) -> None:
    mine, other, workspace = world["mine"], world["other"], world["workspace"]
    with sessions() as db:
        _signal(db, mine, 0, "hold", minutes_after=1)

        def halt(scope_type, scope_id, reason, released=None):
            db.add(
                TradingHalt(
                    workspace_id=workspace.id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    level="entry_halted",
                    reason_code=reason,
                    status="released" if released else "active",
                    halted_at=BASE,
                    released_at=released,
                )
            )

        halt("bot", mine["bot"].id, "data_delay")
        halt("account", mine["account"].id, "consecutive_loss_limit")
        halt("workspace", None, "user_emergency_stop")
        halt("bot", other["bot"].id, "data_delay")  # another bot's
        halt("account", other["account"].id, "peak_drawdown_limit")  # another account's
        halt("bot", mine["bot"].id, "old_one", released=BASE - timedelta(days=30))  # long gone
        db.commit()

    page = _page(sessions, world)

    assert {h.reason_code for h in page.halts} == {
        "data_delay",
        "consecutive_loss_limit",
        "user_emergency_stop",
    }
