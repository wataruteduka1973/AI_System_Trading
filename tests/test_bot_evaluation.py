"""`evaluate_bot_on_latest_bar` had zero callers/coverage before the
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
    RiskProfileVersion,
    Signal,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import TradingAccount, TradingPosition
from app.trading.application.bot_evaluation import evaluate_bot_on_latest_bar


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


# ---- evaluate_bot_on_latest_bar ----


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


def test_evaluate_bot_on_latest_bar_holds_when_bot_is_not_active() -> None:
    db = MagicMock()
    result = evaluate_bot_on_latest_bar(db, _bot(actual_state="stopped"), _bot_run())
    assert result == {"action": "hold", "reason": "bot_not_active", "actual_state": "stopped"}
    db.get.assert_not_called()


def test_evaluate_bot_on_latest_bar_holds_when_no_candles_exist() -> None:
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

    result = evaluate_bot_on_latest_bar(db, bot, _bot_run(bot_id=bot.id))

    assert result == {"action": "hold", "reason": "no_candles"}


def test_evaluate_bot_on_latest_bar_is_idempotent_for_an_already_processed_candle() -> None:
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

    result = evaluate_bot_on_latest_bar(db, bot, bot_run)

    assert result == {"action": "already_processed", "signal_id": existing_signal.id}
    db.add.assert_not_called()  # no second Signal row attempted
    db.commit.assert_called_once_with()  # the (empty) cycle's single commit


# ---- strategy selection (docs/plans/paper-trading-live-data.md Unit 2) ----


def _evaluate_with(definition: object, candles: list[Candle]) -> tuple[MagicMock, TradingBot, dict]:
    """Runs one evaluation through the signal write. Each case here ends in
    "hold", so the Risk Gate/order path is never reached."""
    db = MagicMock()
    bot = _bot()
    db.get.side_effect = [
        TradingAccount(
            id=uuid4(), workspace_id=bot.workspace_id, mode="paper", base_currency="JPY"
        ),
        _instrument(id=bot.instrument_id),
        StrategyVersion(id=bot.strategy_version_id, definition=definition),
    ]
    # db.scalar call order: _exchange_code_for_connection, already-processed guard,
    # the stop check's open-position lookup (flat here).
    db.scalar.side_effect = ["binance", None, None]
    db.scalars.return_value = candles
    result = evaluate_bot_on_latest_bar(db, bot, _bot_run(bot_id=bot.id))
    return db, bot, result


def _flat_candles(count: int) -> list[Candle]:
    start = datetime.now(UTC) - timedelta(hours=4 * count)
    return [
        _candle(
            open_time=start + timedelta(hours=4 * i),
            close_time=start + timedelta(hours=4 * (i + 1)),
        )
        for i in range(count)
    ]


def test_the_signal_comes_from_the_bots_strategy_version_and_records_it() -> None:
    db, _, result = _evaluate_with(
        {"kind": "donchian_breakout", "entry_period": 3, "exit_period": 2}, _flat_candles(5)
    )

    assert result["action"] == "hold"
    [signal] = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], Signal)]
    assert signal.rationale == {
        "kind": "donchian_breakout",
        "parameters": {"entry_period": 3, "exit_period": 2},
        "exit_policy": "signal",
        "close": "100",
    }


def test_the_pipeline_reads_the_same_history_window_as_the_backtest() -> None:
    from app.trading.application.backtest_replay import _HISTORY_WINDOW

    db, _, _ = _evaluate_with(
        {"kind": "donchian_breakout", "entry_period": 3, "exit_period": 2}, _flat_candles(5)
    )

    statement = db.scalars.call_args.args[0]
    assert statement._limit == _HISTORY_WINDOW


def test_an_unresolvable_strategy_definition_stops_the_evaluation() -> None:
    from app.trading.application.live_strategies import UnresolvableStrategyError

    with pytest.raises(UnresolvableStrategyError):
        _evaluate_with({"kind": "unknown"}, _flat_candles(5))


# ---- stop-loss (docs/plans/paper-trading-live-data.md Unit 3) ----


def _bar(i: int, open_: str, high: str, low: str, close: str) -> Candle:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    return _candle(
        open_time=start + timedelta(hours=4 * i),
        close_time=start + timedelta(hours=4 * (i + 1)),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def _held_long(stop: str, opened_at: datetime) -> TradingPosition:
    return TradingPosition(
        id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        side="long",
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        status="open",
        stop_price=Decimal(stop),
        opened_at=opened_at,
    )


# Entry filled just after bar 0 closed (04:00 UTC); bar 0's own range is never checked.
_OPENED = datetime(2026, 10, 1, 4, 0, 5, tzinfo=UTC)


def test_stop_exit_price_is_the_stop_once_a_later_bars_low_reaches_it() -> None:
    from app.trading.application.bot_evaluation import _stop_exit_price

    bars = [_bar(0, "100", "100", "80", "100"), _bar(1, "99", "101", "94", "96")]
    assert _stop_exit_price(bars, _held_long("95", _OPENED)) == Decimal("95")


def test_stop_exit_price_is_the_open_when_a_bar_gaps_through_the_stop() -> None:
    from app.trading.application.bot_evaluation import _stop_exit_price

    bars = [_bar(0, "100", "100", "100", "100"), _bar(1, "90", "91", "88", "89")]
    assert _stop_exit_price(bars, _held_long("95", _OPENED)) == Decimal("90")


def test_stop_exit_price_uses_the_first_bar_that_hits_after_missed_bars() -> None:
    from app.trading.application.bot_evaluation import _stop_exit_price

    bars = [
        _bar(0, "100", "100", "100", "100"),
        _bar(1, "100", "102", "96", "101"),  # missed by the worker, but did not hit
        _bar(2, "93", "94", "90", "92"),  # gapped through -> fill at its open
        _bar(3, "85", "86", "80", "81"),
    ]
    assert _stop_exit_price(bars, _held_long("95", _OPENED)) == Decimal("93")


def test_no_stop_exit_while_no_later_bar_reaches_the_stop() -> None:
    from app.trading.application.bot_evaluation import _stop_exit_price

    bars = [_bar(0, "100", "100", "80", "100"), _bar(1, "99", "110", "96", "108")]
    assert _stop_exit_price(bars, _held_long("95", _OPENED)) is None


def test_no_stop_exit_for_a_position_without_a_stop() -> None:
    from app.trading.application.bot_evaluation import _stop_exit_price

    position = _held_long("95", _OPENED)
    position.stop_price = None
    assert _stop_exit_price([_bar(1, "90", "91", "50", "60")], position) is None


@pytest.mark.parametrize(("side", "expected"), [("buy", Decimal("95")), ("sell", Decimal("105"))])
def test_entry_stop_price_is_the_stop_distance_away_from_the_fill(
    side: str, expected: Decimal
) -> None:
    from app.trading.application.bot_evaluation import _entry_stop_price

    assert _entry_stop_price(side, Decimal("100"), Decimal("5")) == expected


def test_no_entry_stop_when_the_distance_would_put_it_at_or_below_zero() -> None:
    from app.trading.application.bot_evaluation import _entry_stop_price

    assert _entry_stop_price("buy", Decimal("100"), Decimal("100")) is None


# ---- stop-loss inside evaluate_bot_on_latest_bar ----

_DONCHIAN_STOP = {
    "kind": "donchian_breakout",
    "entry_period": 3,
    "exit_period": 2,
    "exit_policy": "stop_loss",
}


@pytest.mark.parametrize("actual_state", ["running", "paused"])
def test_a_reached_stop_closes_the_whole_position_at_the_stop_before_the_signal(
    monkeypatch: pytest.MonkeyPatch, actual_state: str
) -> None:
    from app.models.audit import AuditLog
    from app.trading.application import order_flow

    placed: list[order_flow.PlaceOrderCommand] = []
    monkeypatch.setattr(order_flow, "place_order", lambda db, command, **kw: placed.append(command))
    db = MagicMock()
    bot = _bot(actual_state=actual_state)
    position = _held_long("95", _OPENED)
    db.get.side_effect = [
        TradingAccount(
            id=uuid4(), workspace_id=bot.workspace_id, mode="paper", base_currency="JPY"
        ),
        _instrument(id=bot.instrument_id),
        StrategyVersion(id=bot.strategy_version_id, definition=_DONCHIAN_STOP),
    ]
    # _exchange_code_for_connection, already-processed guard, the stop check's position.
    db.scalar.side_effect = ["binance", None, position]
    db.scalars.return_value = [  # newest first, as the query returns them
        _bar(1, "99", "101", "94", "96"),  # too little history for a signal -> "hold"
        _bar(0, "100", "100", "100", "100"),
    ]

    result = evaluate_bot_on_latest_bar(db, bot, _bot_run(bot_id=bot.id))

    assert result["action"] == "hold"
    [command] = placed
    assert (command.side, command.quantity) == ("sell", Decimal("1"))
    assert command.stop_price == Decimal("95")
    assert command.stop_fill_price == Decimal("95")
    audits = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], AuditLog)]
    assert [a.action for a in audits] == ["protective_exit.stop_loss"]


def _enter_long(
    monkeypatch: pytest.MonkeyPatch,
    definition: dict,
    place_order=None,
    seen_db: list | None = None,
    create_intent=None,
) -> TradingPosition:
    """One "buy" through a stubbed Risk Gate and order flow; returns the position
    the entry opened, as the pipeline sees it afterwards. `place_order` replaces the
    stubbed order placement; `seen_db` receives the session used."""
    from types import SimpleNamespace

    from app.models.trading import Fill
    from app.trading.application import bot_evaluation, order_flow

    decision = SimpleNamespace(
        id=uuid4(), outcome="allow", reason_code=None,
        rule_results={"quantity_calculation": {"stop_distance": "5"}},
    )  # fmt: skip
    monkeypatch.setattr(
        bot_evaluation,
        "evaluate_signal",
        lambda *args: SimpleNamespace(decision=decision, approved_quantity=Decimal("1")),
    )
    monkeypatch.setattr(
        order_flow,
        "create_order_intent",
        create_intent or (lambda db, c, **kw: SimpleNamespace(id=uuid4())),
    )
    order = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(order_flow, "place_order", place_order or (lambda db, c, **kw: order))

    db = MagicMock()
    if seen_db is not None:
        seen_db.append(db)
    bot = _bot()
    opened = _held_long("1", _OPENED)
    opened.stop_price = None
    db.get.side_effect = [
        TradingAccount(
            id=uuid4(), workspace_id=bot.workspace_id, mode="paper", base_currency="JPY"
        ),
        _instrument(id=bot.instrument_id),
        StrategyVersion(id=bot.strategy_version_id, definition=definition),
        RiskProfileVersion(id=bot.risk_profile_version_id),
    ]
    # exchange code, already-processed guard, stop check (flat), the entry's
    # existing-position lookup (flat), then the entry's fill and new position.
    db.scalar.side_effect = ["binance", None, None, None, Fill(price=Decimal("101")), opened]
    bars = [_bar(i, c, c, c, c) for i, c in enumerate(["100"] * 4 + ["101"])]
    candles = list(reversed(bars))  # the query returns newest first
    no_halts = MagicMock(all=MagicMock(return_value=[]))
    db.scalars.side_effect = [candles, no_halts, no_halts, no_halts]  # then the halt lookups

    assert evaluate_bot_on_latest_bar(db, bot, _bot_run(bot_id=bot.id))["action"] == "opened"
    return opened


def test_a_stop_loss_strategy_records_the_stop_on_an_entry_from_flat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    position = _enter_long(monkeypatch, _DONCHIAN_STOP)
    assert position.stop_price == Decimal("96")  # fill 101 - stop_distance 5


def test_a_signal_only_strategy_never_records_a_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = {k: v for k, v in _DONCHIAN_STOP.items() if k != "exit_policy"}
    position = _enter_long(monkeypatch, definition)
    assert position.stop_price is None


def test_an_entry_cycle_commits_once_and_every_stage_defers_to_that_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    flags: list[object] = []

    def intent(db, c, **kw):
        flags.append(kw.get("commit"))
        return SimpleNamespace(id=uuid4())

    def place(db, c, **kw):
        flags.append(kw.get("commit"))
        return SimpleNamespace(id=uuid4())

    seen: list[MagicMock] = []
    _enter_long(monkeypatch, _DONCHIAN_STOP, place_order=place, seen_db=seen, create_intent=intent)

    assert flags == [False, False]
    db = seen[0]
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_a_failure_after_the_signal_rolls_the_whole_cycle_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the per-stage commits: the Signal used to be committed before the
    order was placed, so a failing order left the candle marked processed and the
    entry was never retried."""
    from app.trading.application.order_flow import OrderFlowError

    def failing_place(db, c, **kw):
        raise OrderFlowError("market_price_unavailable", "no candle")

    seen: list[MagicMock] = []
    with pytest.raises(OrderFlowError):
        _enter_long(monkeypatch, _DONCHIAN_STOP, place_order=failing_place, seen_db=seen)

    db = seen[0]
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


def test_an_entry_blocked_by_an_active_halt_is_an_outcome_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`place_order` raises on a halted entry; raising here would roll the whole cycle back and
    retry it on every poll until the halt lifts."""
    from types import SimpleNamespace

    from app.trading.application import bot_evaluation, order_flow

    decision = SimpleNamespace(
        id=uuid4(), outcome="allow", reason_code=None,
        rule_results={"quantity_calculation": {"stop_distance": "5"}},
    )  # fmt: skip
    monkeypatch.setattr(
        bot_evaluation,
        "evaluate_signal",
        lambda *args: SimpleNamespace(decision=decision, approved_quantity=Decimal("1")),
    )
    monkeypatch.setattr(order_flow, "entry_blocked_by_halt", lambda *a, **k: True)
    intent = MagicMock()
    place = MagicMock()
    monkeypatch.setattr(order_flow, "create_order_intent", intent)
    monkeypatch.setattr(order_flow, "place_order", place)

    db = MagicMock()
    bot = _bot()
    db.get.side_effect = [
        TradingAccount(
            id=uuid4(), workspace_id=bot.workspace_id, mode="paper", base_currency="JPY"
        ),
        _instrument(id=bot.instrument_id),
        StrategyVersion(id=bot.strategy_version_id, definition=_DONCHIAN_STOP),
        RiskProfileVersion(id=bot.risk_profile_version_id),
    ]
    db.scalar.side_effect = ["binance", None, None, None]
    bars = [_bar(i, c, c, c, c) for i, c in enumerate(["100"] * 4 + ["101"])]
    db.scalars.return_value = list(reversed(bars))

    result = evaluate_bot_on_latest_bar(db, bot, _bot_run(bot_id=bot.id))

    assert result["action"] == "halted"
    intent.assert_not_called()
    place.assert_not_called()
    db.commit.assert_called_once_with()  # the signal and the decision are kept
    db.rollback.assert_not_called()
