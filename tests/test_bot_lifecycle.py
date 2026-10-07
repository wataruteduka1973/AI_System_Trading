from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.connections import Exchange, ExchangeConnection
from app.models.market_data import Candle
from app.models.strategy import (
    BotRun,
    RiskProfileVersion,
    StrategyVersion,
    TradingBot,
    TradingHalt,
)
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
        id=uuid4(),
        strategy_id=uuid4(),
        version=1,
        lifecycle_status="paper_approved",
        definition={"kind": "donchian_breakout", "entry_period": 55, "exit_period": 20},
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


@pytest.mark.parametrize("scope_type", ["bot", "workspace"])
def test_validate_bot_startup_refuses_while_an_emergency_stop_is_active(scope_type: str) -> None:
    """The emergency stop is released only by an Owner; neither start nor resume may bring
    a bot back under it (both run this validation)."""
    bot = _bot()
    db = MagicMock()
    db.get.side_effect = [_connection(), _account()]
    emergency = TradingHalt(
        id=uuid4(),
        workspace_id=bot.workspace_id,
        scope_type=scope_type,
        scope_id=bot.id if scope_type == "bot" else None,
        level="emergency_stopped",
        reason_code="user_emergency_stop",
        status="active",
    )
    # one scope lookup per scope type, bot first; only the matching one has the halt
    empty = MagicMock(all=MagicMock(return_value=[]))
    holding = MagicMock(all=MagicMock(return_value=[emergency]))
    db.scalars.side_effect = [holding] if scope_type == "bot" else [empty, holding]

    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, bot)

    assert exc.value.code == "startup_validation_failed_emergency_stop"


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
        side_effect=lambda db_, order, *, reason_code, commit=True: (
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
    # db.scalar call order in resume_bot: 1) paused BotRun lookup, then inside
    # validate_bot_startup 2) the instrument's exchange code, 3) the latest candle.
    db.scalar.side_effect = [paused_run, "binance", _candle()]
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


# ---- validate_bot_startup: strategy definition and public-price instruments ----
# (docs/plans/paper-trading-live-data.md Unit 2)


def test_validate_bot_startup_fails_on_an_unresolvable_strategy_definition() -> None:
    db = MagicMock()
    db.get.side_effect = [
        _connection(),
        _account(),
        _strategy_version(definition={"kind": "not-a-strategy"}),
    ]
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_strategy"


def test_validate_bot_startup_refuses_a_public_price_instrument_on_a_non_binance_connection() -> (
    None
):
    db = MagicMock()
    db.get.side_effect = [
        _connection(),
        _account(),
        _strategy_version(),
        _risk_profile_version(),
        Exchange(id=uuid4(), code="oanda", name="OANDA"),  # the connection's exchange
    ]
    db.scalar.side_effect = ["binance_public"]  # the instrument's exchange
    with pytest.raises(lifecycle.BotLifecycleError) as exc:
        lifecycle.validate_bot_startup(db, _bot())
    assert exc.value.code == "startup_validation_failed_instrument"


def test_validate_bot_startup_accepts_a_public_price_instrument_on_a_binance_connection() -> None:
    db = MagicMock()
    db.get.side_effect = [
        _connection(),
        _account(),
        _strategy_version(),
        _risk_profile_version(),
        Exchange(id=uuid4(), code="binance", name="Binance"),
    ]
    db.scalar.side_effect = ["binance_public", _candle()]
    lifecycle.validate_bot_startup(db, _bot())  # does not raise


# ---- fail_bot (docs/plans/worker-failure-handling.md) ----


def _fail(db, bot, **kwargs):
    return lifecycle.fail_bot(db, bot, error_type="ValueError", consecutive_failures=5, **kwargs)


def _failing_db(bot_run, holds_position=False, orders=()):
    db = MagicMock()
    db.scalar.side_effect = [bot_run, uuid4() if holds_position else None]
    db.scalars.return_value.all.return_value = list(orders)
    return db


def test_fail_bot_ends_the_run_as_failed_and_leaves_the_bot_startable() -> None:
    bot = _bot(desired_state="running", actual_state="running")
    bot_run = BotRun(id=uuid4(), bot_id=bot.id, status="running")
    db = _failing_db(bot_run)
    version = bot.version

    _fail(db, bot)

    assert (bot.desired_state, bot.actual_state) == ("stopped", "failed")
    assert bot.version == version + 1
    assert bot_run.status == "failed"
    assert bot_run.stopped_at is not None
    assert bot_run.stop_reason == "evaluation failed: ValueError"
    db.commit.assert_called_once_with()


def test_a_failed_bot_can_be_started_again() -> None:
    """`desired_state` is `stopped`, which is what `start_bot` accepts."""
    bot = _bot(desired_state="running", actual_state="running")
    _fail(_failing_db(BotRun(id=uuid4(), bot_id=bot.id, status="running")), bot)

    assert bot.desired_state == "stopped"
    assert bot.actual_state == "failed"  # distinct from an ordinary stop


def test_fail_bot_announces_it_without_the_exception_message() -> None:
    from app.models.audit import AuditLog, OutboxEvent, SystemEvent

    bot = _bot(name="btcusdt-4h")
    db = _failing_db(BotRun(id=uuid4(), bot_id=bot.id, status="running"))

    _fail(db, bot)

    added = [c.args[0] for c in db.add.call_args_list]
    (event,) = [o for o in added if isinstance(o, SystemEvent)]
    (outbox,) = [o for o in added if isinstance(o, OutboxEvent)]
    assert (event.severity, event.category, event.event_type) == ("error", "system", "bot_failed")
    assert "btcusdt-4h" in event.message and "5回続けて失敗" in event.message
    assert "建玉" not in event.message
    assert event.payload == {
        "bot_id": str(bot.id),
        "bot_name": "btcusdt-4h",
        "error_type": "ValueError",
        "consecutive_failures": 5,
        "holds_position": False,
    }
    assert outbox.correlation_id == event.correlation_id
    assert outbox.aggregate_id == bot.id
    (audit,) = [o for o in added if isinstance(o, AuditLog)]
    assert audit.action == "trading_bot.failed"


def test_the_notice_warns_when_a_position_is_left_unwatched() -> None:
    from app.models.audit import SystemEvent

    bot = _bot()
    db = _failing_db(BotRun(id=uuid4(), bot_id=bot.id, status="running"), holds_position=True)

    _fail(db, bot)

    (event,) = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], SystemEvent)]
    assert event.payload["holds_position"] is True
    assert "建玉が残っており、損切りの監視も止まっています" in event.message


def test_fail_bot_cancels_open_orders_with_the_callers_transaction(monkeypatch) -> None:
    from app.trading.application import order_flow

    bot = _bot()
    order = TradeOrder(id=uuid4(), workspace_id=bot.workspace_id, status="submitted")
    db = _failing_db(BotRun(id=uuid4(), bot_id=bot.id, status="running"), orders=[order])
    cancel = MagicMock(side_effect=lambda db_, o, *, reason_code, commit: o)
    monkeypatch.setattr(order_flow, "cancel_order", cancel)

    _fail(db, bot)

    cancel.assert_called_once_with(db, order, reason_code="bot_failed", commit=False)
    db.commit.assert_called_once_with()  # one commit for the whole unit


def test_fail_bot_works_without_a_bot_run() -> None:
    bot = _bot()
    db = _failing_db(None)

    assert _fail(db, bot) is None

    assert bot.actual_state == "failed"
    db.commit.assert_called_once_with()
