"""`ensure_dummy_bot` had zero callers and zero test coverage before the Bot
management API task (`app/api/routes/trading.py`) started calling it
directly. See `dummy_pipeline.py`'s module docstring for the
provisioning-vs-starting split these tests cover -- the old version of this
function auto-started the bot it created via `bot_lifecycle.start_bot`; the
new version never touches `desired_state`/`actual_state` at all.
"""

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.instruments import Instrument
from app.models.strategy import (
    RiskProfile,
    RiskProfileVersion,
    Strategy,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import TradingAccount
from app.trading.application.dummy_pipeline import ensure_dummy_bot


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
