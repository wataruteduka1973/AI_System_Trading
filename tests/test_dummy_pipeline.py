"""`ensure_dummy_bot` had zero callers and zero test coverage before the Bot
management API task (`app/api/routes/trading.py`) started calling it
directly. See `dummy_pipeline.py`'s module docstring for the
provisioning-vs-starting split these tests cover -- the old version of this
function auto-started the bot it created via `bot_lifecycle.start_bot`; the
new version never touches `desired_state`/`actual_state` at all.

`run_dummy_pipeline_once` likewise had zero callers/coverage before the
execution loop/Worker task (`app/trading/application/bot_execution_loop.py`)
gave it its first real caller. The tests below cover the gating branches and,
in particular, the idempotency guard added for that task -- see that
function's own docstring for why a Worker polling on an interval needed it.
Full coverage of the opened/denied/close_only paths (pre-existing,
`order_flow`/`risk_gate` logic this task did not touch) is not attempted here.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import (
    BotRun,
    RiskProfile,
    RiskProfileVersion,
    Signal,
    Strategy,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import TradingAccount
from app.trading.application.dummy_pipeline import ensure_dummy_bot, run_dummy_pipeline_once


def _account(**overrides: object) -> TradingAccount:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        connection_id=uuid4(),
        mode="paper",
        base_currency="JPY",
        status="active",
    )
    defaults.update(overrides)
    return TradingAccount(**defaults)


def _instrument(**overrides: object) -> Instrument:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        exchange_id=uuid4(),
        market_id=uuid4(),
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_scale=2,
        quantity_scale=6,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.000001"),
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def test_ensure_dummy_bot_creates_everything_when_nothing_exists() -> None:
    db = MagicMock()
    # db.scalar call order: Strategy, StrategyVersion, RiskProfile,
    # RiskProfileVersion, TradingBot -- all missing.
    db.scalar.side_effect = [None, None, None, None, None]
    workspace_id = uuid4()
    account = _account(workspace_id=workspace_id)
    instrument = _instrument()

    bot = ensure_dummy_bot(db, workspace_id, account, instrument, bot_name="bot-1")

    assert isinstance(bot, TradingBot)
    assert bot.name == "bot-1"
    assert bot.account_id == account.id
    assert bot.instrument_id == instrument.id
    assert bot.connection_id == account.connection_id
    # Never touched: the old version set these via bot_lifecycle.start_bot.
    # Leaving them unset here (server_default applies only at DB insert time)
    # is the regression signal that auto-start was removed.
    assert bot.desired_state is None
    assert bot.actual_state is None
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(bot)


def test_ensure_dummy_bot_reuses_existing_rows_and_does_not_touch_bot_state() -> None:
    db = MagicMock()
    workspace_id = uuid4()
    account = _account(workspace_id=workspace_id)
    instrument = _instrument()
    strategy = Strategy(id=uuid4(), workspace_id=workspace_id, name="s", mode="technical")
    strategy_version = StrategyVersion(
        id=uuid4(), strategy_id=strategy.id, version=1, lifecycle_status="paper_approved"
    )
    risk_profile = RiskProfile(id=uuid4(), workspace_id=workspace_id, name="r")
    risk_profile_version = RiskProfileVersion(
        id=uuid4(), risk_profile_id=risk_profile.id, version=1, status="approved"
    )
    existing_bot = TradingBot(
        id=uuid4(),
        workspace_id=workspace_id,
        name="bot-1",
        execution_mode="paper",
        strategy_mode="technical",
        connection_id=account.connection_id,
        account_id=account.id,
        instrument_id=instrument.id,
        timeframe="1m",
        strategy_version_id=strategy_version.id,
        risk_profile_version_id=risk_profile_version.id,
        desired_state="running",
        actual_state="running",
    )
    db.scalar.side_effect = [
        strategy,
        strategy_version,
        risk_profile,
        risk_profile_version,
        existing_bot,
    ]

    bot = ensure_dummy_bot(db, workspace_id, account, instrument, bot_name="bot-1")

    assert bot is existing_bot
    assert bot.desired_state == "running"  # untouched -- not reset or re-started
    db.add.assert_not_called()  # nothing new created


def test_ensure_dummy_bot_raises_if_account_has_no_connection() -> None:
    db = MagicMock()
    db.scalar.side_effect = [None, None, None, None, None]
    workspace_id = uuid4()
    account = _account(workspace_id=workspace_id, connection_id=None)
    instrument = _instrument()

    with pytest.raises(ValueError, match="connection_id"):
        ensure_dummy_bot(db, workspace_id, account, instrument, bot_name="bot-1")


# ---- run_dummy_pipeline_once ----


def _bot(**overrides: object) -> TradingBot:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        name="bot",
        execution_mode="paper",
        strategy_mode="technical",
        connection_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        desired_state="running",
        actual_state="running",
        version=1,
    )
    defaults.update(overrides)
    return TradingBot(**defaults)


def _bot_run(**overrides: object) -> BotRun:
    defaults: dict[str, object] = dict(id=uuid4(), bot_id=uuid4(), status="running")
    defaults.update(overrides)
    return BotRun(**defaults)


def _candle(**overrides: object) -> Candle:
    now = datetime.now(UTC)
    defaults: dict[str, object] = dict(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=now - timedelta(minutes=1),
        close_time=now,
        open=100,
        high=100,
        low=100,
        close=100,
        source="test",
        is_final=True,
    )
    defaults.update(overrides)
    return Candle(**defaults)


def test_run_dummy_pipeline_once_holds_when_bot_is_not_active() -> None:
    db = MagicMock()
    result = run_dummy_pipeline_once(db, _bot(actual_state="stopped"), _bot_run())
    assert result == {"action": "hold", "reason": "bot_not_active", "actual_state": "stopped"}
    db.get.assert_not_called()


def test_run_dummy_pipeline_once_holds_when_no_candles_exist() -> None:
    db = MagicMock()
    bot = _bot()
    db.get.side_effect = [
        TradingAccount(
            id=uuid4(), workspace_id=bot.workspace_id, mode="paper", base_currency="JPY"
        ),
        _instrument(id=bot.instrument_id),
    ]
    db.scalar.return_value = "binance"  # _exchange_code_for_connection
    db.scalars.return_value = []  # list(db.scalars(...)) -- no .all() call in this path

    result = run_dummy_pipeline_once(db, bot, _bot_run(bot_id=bot.id))

    assert result == {"action": "hold", "reason": "no_candles"}


def test_run_dummy_pipeline_once_is_idempotent_for_an_already_processed_candle() -> None:
    """Regression test for the execution-loop task: before this guard existed,
    calling this function twice for the same still-latest candle would reach
    `db.commit()` a second time and raise an uncaught IntegrityError against
    `uq_signal_idempotency` -- see the function's own docstring."""
    db = MagicMock()
    bot = _bot()
    bot_run = _bot_run(bot_id=bot.id)
    candle = _candle(instrument_id=bot.instrument_id, timeframe=bot.timeframe)
    account = TradingAccount(
        id=uuid4(), workspace_id=bot.workspace_id, mode="paper", base_currency="JPY"
    )
    instrument = _instrument(id=bot.instrument_id)
    db.get.side_effect = [account, instrument]
    existing_signal = Signal(
        id=uuid4(),
        workspace_id=bot.workspace_id,
        bot_run_id=bot_run.id,
        candle_id=candle.id,
        strategy_version_id=bot.strategy_version_id,
        action="buy",
        rationale={},
        input_checksum="x",
    )
    # db.scalar call order: _exchange_code_for_connection, then the
    # already-processed guard's Signal lookup.
    db.scalar.side_effect = ["binance", existing_signal]
    db.scalars.return_value = [candle]  # list(db.scalars(...)) -- no .all() call in this path

    result = run_dummy_pipeline_once(db, bot, bot_run)

    assert result == {"action": "already_processed", "signal_id": existing_signal.id}
    db.add.assert_not_called()  # no second Signal row attempted
    db.commit.assert_not_called()
