from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.connections import ExchangeConnection
from app.models.market_data import Candle
from app.models.strategy import BotRun, RiskProfileVersion, StrategyVersion, TradingBot
from app.models.trading import TradeOrder, TradingAccount
from app.trading.application import bot_lifecycle as lifecycle


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
        desired_state="stopped",
        actual_state="stopped",
        version=1,
    )
    defaults.update(overrides)
    return TradingBot(**defaults)


def _connection(**overrides: object) -> ExchangeConnection:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        exchange_id=uuid4(),
        label="c",
        environment="practice",
        api_base_url="https://x",
        secret_ref="local-encrypted://x",
        status="verified",
    )
    defaults.update(overrides)
    return ExchangeConnection(**defaults)


def _account(**overrides: object) -> TradingAccount:
    defaults: dict[str, object] = dict(
        id=uuid4(), workspace_id=uuid4(), mode="paper", base_currency="JPY", status="active"
    )
    defaults.update(overrides)
    return TradingAccount(**defaults)


def _strategy_version(**overrides: object) -> StrategyVersion:
    defaults: dict[str, object] = dict(
        id=uuid4(), strategy_id=uuid4(), version=1, lifecycle_status="paper_approved"
    )
    defaults.update(overrides)
    return StrategyVersion(**defaults)


def _risk_profile_version(**overrides: object) -> RiskProfileVersion:
    defaults: dict[str, object] = dict(
        id=uuid4(), risk_profile_id=uuid4(), version=1, status="approved"
    )
    defaults.update(overrides)
    return RiskProfileVersion(**defaults)


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


# ---- validate_bot_startup ----


def test_validate_bot_startup_passes_with_all_checks_satisfied() -> None:
    db = MagicMock()
    bot = _bot()
    db.get.side_effect = [
        _connection(),
        _account(),
        _strategy_version(),
        _risk_profile_version(),
    ]
    db.scalar.return_value = _candle()
    lifecycle.validate_bot_startup(db, bot)  # does not raise


def test_validate_bot_startup_fails_on_unverified_connection() -> None:
    db = MagicMock()
    db.get.return_value = _connection(status="pending_credentials")
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_secrets"


def test_validate_bot_startup_fails_on_live_execution_mode() -> None:
    db = MagicMock()
    db.get.side_effect = [_connection()]
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot(execution_mode="live"))
    assert exc.value.code == "startup_validation_failed_trading_permission"


def test_validate_bot_startup_fails_on_inactive_account() -> None:
    db = MagicMock()
    db.get.side_effect = [_connection(), _account(status="disabled")]
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_account"


def test_validate_bot_startup_fails_on_draft_strategy() -> None:
    db = MagicMock()
    db.get.side_effect = [_connection(), _account(), _strategy_version(lifecycle_status="draft")]
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_strategy"


def test_validate_bot_startup_fails_on_unapproved_risk_profile() -> None:
    db = MagicMock()
    db.get.side_effect = [
        _connection(),
        _account(),
        _strategy_version(),
        _risk_profile_version(status="draft"),
    ]
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_risk_profile"


def test_validate_bot_startup_fails_on_stale_candle() -> None:
    db = MagicMock()
    db.get.side_effect = [_connection(), _account(), _strategy_version(), _risk_profile_version()]
    stale = _candle(close_time=datetime.now(UTC) - timedelta(minutes=30))
    db.scalar.return_value = stale
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_data_freshness"


# ---- start_bot ----


def test_start_bot_rejects_already_running() -> None:
    db = MagicMock()
    with pytest.raises(lifecycle.BotStateConflictError):
        lifecycle.start_bot(db, _bot(desired_state="running"))


def test_start_bot_rejects_from_paused() -> None:
    db = MagicMock()
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.start_bot(db, _bot(desired_state="paused"))
    assert exc.value.code == "invalid_state"


def test_start_bot_creates_a_new_bot_run_and_transitions_state() -> None:
    db = MagicMock()
    bot = _bot()
    db.get.side_effect = [_connection(), _account(), _strategy_version(), _risk_profile_version()]
    db.scalar.return_value = _candle()
    bot_run = lifecycle.start_bot(db, bot)
    assert bot.desired_state == "running"
    assert bot.actual_state == "running"
    assert isinstance(bot_run, BotRun)
    assert bot_run.status == "running"
    db.commit.assert_called_once()


# ---- pause_bot / stop_bot: cancel open orders, 409s ----


def test_pause_bot_rejects_already_paused() -> None:
    db = MagicMock()
    with pytest.raises(lifecycle.BotStateConflictError):
        lifecycle.pause_bot(db, _bot(desired_state="paused"))


def test_pause_bot_rejects_from_stopped() -> None:
    db = MagicMock()
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.pause_bot(db, _bot(desired_state="stopped"))
    assert exc.value.code == "invalid_state"


def test_pause_bot_cancels_open_orders_and_keeps_the_same_bot_run() -> None:
    db = MagicMock()
    bot = _bot(desired_state="running", actual_state="running")
    running_run = BotRun(id=uuid4(), bot_id=bot.id, status="running", code_version="x")
    open_order = TradeOrder(
        id=uuid4(),
        workspace_id=bot.workspace_id,
        account_id=bot.account_id,
        client_order_id="c1",
        instrument_id=bot.instrument_id,
        side="buy",
        order_type="limit",
        time_in_force="gtc",
        quantity=1,
        status="submitted",
    )
    db.scalar.side_effect = [running_run]
    db.scalars.return_value.all.return_value = [open_order]

    from app.trading.application import order_flow

    original_cancel = order_flow.cancel_order
    order_flow.cancel_order = MagicMock(
        side_effect=lambda db_, order, *, reason_code: (
            setattr(order, "status", "cancelled") or order
        )
    )
    try:
        bot_run = lifecycle.pause_bot(db, bot)
    finally:
        order_flow.cancel_order = original_cancel

    assert bot.desired_state == "paused"
    assert bot.actual_state == "paused"
    assert bot_run is running_run
    assert bot_run.status == "paused"
    assert open_order.status == "cancelled"


def test_stop_bot_rejects_already_stopped() -> None:
    db = MagicMock()
    with pytest.raises(lifecycle.BotStateConflictError):
        lifecycle.stop_bot(db, _bot(desired_state="stopped"))


def test_stop_bot_ends_the_bot_run() -> None:
    db = MagicMock()
    bot = _bot(desired_state="running", actual_state="running")
    running_run = BotRun(id=uuid4(), bot_id=bot.id, status="running", code_version="x")
    db.scalar.side_effect = [running_run]
    db.scalars.return_value.all.return_value = []

    bot_run = lifecycle.stop_bot(db, bot)
    assert bot.desired_state == "stopped"
    assert bot.actual_state == "stopped"
    assert bot_run.status == "stopped"
    assert bot_run.stopped_at is not None
    assert bot_run.stop_reason == "stop command"


# ---- resume_bot: reuses BotRun, validation failure leaves state untouched ----


def test_resume_bot_rejects_already_running() -> None:
    db = MagicMock()
    with pytest.raises(lifecycle.BotStateConflictError):
        lifecycle.resume_bot(db, _bot(desired_state="running"))


def test_resume_bot_reuses_the_paused_bot_run_and_revalidates() -> None:
    db = MagicMock()
    bot = _bot(desired_state="paused", actual_state="paused")
    paused_run = BotRun(id=uuid4(), bot_id=bot.id, status="paused", code_version="x")
    # db.scalar call order in resume_bot: 1) paused BotRun lookup, 2) (inside
    # validate_bot_startup) the latest-candle lookup.
    db.scalar.side_effect = [paused_run, _candle()]
    db.get.side_effect = [_connection(), _account(), _strategy_version(), _risk_profile_version()]

    result = lifecycle.resume_bot(db, bot)
    assert bot.desired_state == "running"
    assert bot.actual_state == "running"
    assert result is paused_run
    assert paused_run.status == "running"


def test_resume_bot_validation_failure_leaves_state_untouched() -> None:
    db = MagicMock()
    bot = _bot(desired_state="paused", actual_state="paused")
    paused_run = BotRun(id=uuid4(), bot_id=bot.id, status="paused", code_version="x")
    db.scalar.return_value = paused_run
    db.get.side_effect = [_connection(status="pending_credentials")]

    with pytest.raises(lifecycle.BotLifecycleError):
        lifecycle.resume_bot(db, bot)

    assert bot.desired_state == "paused"  # untouched
    assert bot.actual_state == "paused"
    assert paused_run.status == "paused"  # untouched
    db.commit.assert_not_called()
