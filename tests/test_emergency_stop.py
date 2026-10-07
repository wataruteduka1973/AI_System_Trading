"""The user's emergency stop (app/trading/application/emergency_stop.py): halt, bot stops,
audit/event rows in one transaction; closing the positions afterwards, best effort."""

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.audit import AuditLog, OutboxEvent, SystemEvent
from app.models.strategy import TradingBot, TradingHalt
from app.models.trading import TradingPosition
from app.trading.application import bot_lifecycle, emergency_stop, order_flow


def _bot(workspace_id=None, desired_state="running") -> TradingBot:
    return TradingBot(
        id=uuid4(),
        workspace_id=workspace_id or uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        desired_state=desired_state,
        actual_state=desired_state,
    )


def _db(*, existing_halt: TradingHalt | None = None) -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = existing_halt
    db.begin_nested.return_value.__exit__.return_value = False  # never swallow an exception
    return db


def _added(db: MagicMock, kind: type) -> list:
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], kind)]


@pytest.fixture
def stops(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_stop(db, bot, *, reason=None, commit=True):
        calls.append({"bot_id": bot.id, "reason": reason, "commit": commit})
        bot.desired_state = bot.actual_state = "stopped"

    monkeypatch.setattr(bot_lifecycle, "stop_bot", fake_stop)
    return calls


def test_a_bot_emergency_stop_halts_stops_and_records_who_in_one_commit(stops) -> None:
    bot = _bot()
    db = _db()
    user = uuid4()

    result = emergency_stop.emergency_stop_bot(db, bot, requested_by=user, reason="exchange outage")

    (halt,) = _added(db, TradingHalt)
    assert (halt.scope_type, halt.scope_id, halt.level) == ("bot", bot.id, "emergency_stopped")
    assert halt.reason_code == "user_emergency_stop"
    assert halt.auto_releasable is False
    (event,) = _added(db, SystemEvent)
    assert (event.severity, event.category, event.source_id) == ("critical", "risk", user)
    assert halt.trigger_event_id == event.id
    (outbox,) = _added(db, OutboxEvent)  # announced: the notification worker will pick it up
    assert (outbox.event_type, outbox.correlation_id) == (
        "user_emergency_stop",
        event.correlation_id,
    )
    assert (
        event.payload["close_positions"] is False and event.payload["reason"] == "exchange outage"
    )
    (audit,) = _added(db, AuditLog)
    assert audit.actor_id == user
    assert audit.action == "trading_halt.user_emergency_stop"
    assert audit.correlation_id == event.correlation_id
    assert audit.after_data["reason"] == "exchange outage"
    assert stops == [{"bot_id": bot.id, "reason": "emergency stop", "commit": False}]
    assert result.stopped_bot_ids == [bot.id]
    assert result.already_active is False
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_repeating_it_while_the_halt_is_active_changes_nothing(stops) -> None:
    bot = _bot(desired_state="stopped")
    scope_halt = TradingHalt(
        id=uuid4(),
        workspace_id=bot.workspace_id,
        scope_type="bot",
        scope_id=bot.id,
        level="emergency_stopped",
        reason_code="user_emergency_stop",
        status="active",
    )
    db = _db(existing_halt=scope_halt)

    result = emergency_stop.emergency_stop_bot(db, bot, requested_by=uuid4())

    assert result.already_active is True
    assert result.halt is scope_halt
    assert _added(db, TradingHalt) == [] and _added(db, SystemEvent) == []
    assert stops == []  # already stopped
    db.commit.assert_called_once_with()


def test_a_bot_that_cannot_be_stopped_is_reported_but_the_halt_still_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_stop(db, bot, *, reason=None, commit=True):
        raise bot_lifecycle.BotLifecycleError("no_active_bot_run", "no run")

    monkeypatch.setattr(bot_lifecycle, "stop_bot", failing_stop)
    bot = _bot()
    db = _db()

    result = emergency_stop.emergency_stop_bot(db, bot, requested_by=uuid4())

    assert result.stopped_bot_ids == []
    assert result.bot_stop_failures == [{"bot_id": str(bot.id), "code": "no_active_bot_run"}]
    assert len(_added(db, TradingHalt)) == 1
    db.commit.assert_called_once_with()


def test_a_workspace_emergency_stop_uses_the_workspace_scope_and_every_active_bot(stops) -> None:
    workspace_id = uuid4()
    running, paused, stopped = (
        _bot(workspace_id, "running"),
        _bot(workspace_id, "paused"),
        _bot(workspace_id, "stopped"),
    )
    db = _db()
    db.scalars.return_value.all.return_value = [running, paused, stopped]

    result = emergency_stop.emergency_stop_workspace(db, workspace_id, requested_by=uuid4())

    (halt,) = _added(db, TradingHalt)
    assert (halt.scope_type, halt.scope_id) == ("workspace", None)
    assert result.stopped_bot_ids == [running.id, paused.id]
    assert {call["bot_id"] for call in stops} == {running.id, paused.id}


def test_a_failure_while_activating_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot_lifecycle, "stop_bot", MagicMock())
    db = _db()
    db.flush.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        emergency_stop.emergency_stop_bot(db, _bot(), requested_by=uuid4())

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def _position(bot: TradingBot, side="long", quantity="2") -> TradingPosition:
    return TradingPosition(
        id=uuid4(),
        account_id=bot.account_id,
        instrument_id=bot.instrument_id,
        side=side,
        quantity=Decimal(quantity),
        status="open",
    )


def test_closing_happens_after_the_stop_is_committed_and_does_not_need_the_halt_lifted(
    stops, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot()
    other = _bot(bot.workspace_id)
    db = _db()
    db.scalars.return_value.all.return_value = [_position(bot, "long", "2"), _position(other)]
    order_of_events: list[str] = []
    db.commit.side_effect = lambda: order_of_events.append("commit")
    placed: list[order_flow.PlaceOrderCommand] = []

    def fake_place(db_, command, **kwargs):
        order_of_events.append("close")
        placed.append(command)
        return MagicMock(id=uuid4())

    monkeypatch.setattr(order_flow, "place_order", fake_place)

    result = emergency_stop.emergency_stop_bot(db, bot, requested_by=uuid4(), close_positions=True)

    assert order_of_events == ["commit", "close"]
    (command,) = placed  # the other bot's position is out of scope
    assert (command.side, command.quantity, command.order_type) == ("sell", Decimal("2"), "market")
    assert command.account_id == bot.account_id
    assert len(result.closing_order_ids) == 1


def test_a_position_that_cannot_be_closed_is_reported_and_does_not_undo_the_stop(
    stops, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot()
    db = _db()
    first, second = _position(bot, "short", "1"), _position(bot, "long", "3")
    db.scalars.return_value.all.return_value = [first, second]
    calls = []

    def fake_place(db_, command, **kwargs):
        calls.append(command.side)
        if len(calls) == 1:
            raise order_flow.OrderFlowError("market_price_unavailable", "no price")
        return MagicMock(id=uuid4())

    monkeypatch.setattr(order_flow, "place_order", fake_place)

    result = emergency_stop.emergency_stop_bot(db, bot, requested_by=uuid4(), close_positions=True)

    assert calls == ["buy", "sell"]  # a short is closed by buying; the second still ran
    assert result.close_failures == [
        {"position_id": str(first.id), "code": "market_price_unavailable"}
    ]
    assert len(result.closing_order_ids) == 1
    assert result.stopped_bot_ids == [bot.id]
    db.rollback.assert_called_once_with()  # only the failed close


def test_without_the_close_policy_no_order_is_placed(
    stops, monkeypatch: pytest.MonkeyPatch
) -> None:
    place = MagicMock()
    monkeypatch.setattr(order_flow, "place_order", place)

    emergency_stop.emergency_stop_bot(_db(), _bot(), requested_by=uuid4())

    place.assert_not_called()
