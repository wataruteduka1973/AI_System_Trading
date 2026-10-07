"""`run_active_bots_once` (execution loop/Worker task,
docs/architecture/architecture-alignment-and-long-term-roadmap.md 2026-09-25 "Bot管理API ->
実行ループ/Worker -> 最低限のUI"順). Covers bot discovery, the missing-BotRun
skip path, and that one bot's failure does not stop the rest of the batch --
`evaluate_bot_on_latest_bar` itself is covered separately in
tests/test_bot_evaluation.py.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from app.models.strategy import BotRun, TradingBot
from app.trading.application import bot_execution_loop


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
    )
    defaults.update(overrides)
    return TradingBot(**defaults)


def _bot_run(**overrides: object) -> BotRun:
    defaults: dict[str, object] = dict(id=uuid4(), bot_id=uuid4(), status="running")
    defaults.update(overrides)
    return BotRun(**defaults)


def test_run_active_bots_once_evaluates_every_active_bot(monkeypatch) -> None:
    db = MagicMock()
    bot1, bot2 = _bot(), _bot()
    run1, run2 = _bot_run(bot_id=bot1.id), _bot_run(bot_id=bot2.id)
    db.scalars.return_value.all.return_value = [bot1, bot2]
    db.scalar.side_effect = [run1, run2]
    calls = []
    monkeypatch.setattr(
        bot_execution_loop,
        "evaluate_bot_on_latest_bar",
        lambda db_, bot, bot_run: calls.append((bot, bot_run)) or {"action": "hold"},
    )

    evaluated = bot_execution_loop.run_active_bots_once(db)

    assert evaluated == 2
    assert calls == [(bot1, run1), (bot2, run2)]


def test_run_active_bots_once_returns_zero_when_no_bots_are_active() -> None:
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    assert bot_execution_loop.run_active_bots_once(db) == 0


def test_run_active_bots_once_skips_a_bot_with_no_matching_bot_run(monkeypatch) -> None:
    db = MagicMock()
    bot = _bot()
    db.scalars.return_value.all.return_value = [bot]
    db.scalar.return_value = None  # no active BotRun found
    called = MagicMock()
    monkeypatch.setattr(bot_execution_loop, "evaluate_bot_on_latest_bar", called)

    evaluated = bot_execution_loop.run_active_bots_once(db)

    assert evaluated == 0
    called.assert_not_called()


def test_run_active_bots_once_isolates_one_bots_failure_from_the_rest(monkeypatch) -> None:
    db = MagicMock()
    failing_bot, healthy_bot = _bot(), _bot()
    failing_run, healthy_run = _bot_run(bot_id=failing_bot.id), _bot_run(bot_id=healthy_bot.id)
    db.scalars.return_value.all.return_value = [failing_bot, healthy_bot]
    db.scalar.side_effect = [failing_run, healthy_run]

    def fake_run(db_, bot, bot_run):
        if bot is failing_bot:
            raise RuntimeError("boom")
        return {"action": "hold"}

    monkeypatch.setattr(bot_execution_loop, "evaluate_bot_on_latest_bar", fake_run)

    evaluated = bot_execution_loop.run_active_bots_once(db)

    assert evaluated == 1  # only the healthy bot counted
    db.rollback.assert_called_once()


# ---- failures: counting, failing a bot, heartbeats (docs/plans/worker-failure-handling.md) ----


def _pass(db, bots, runs, monkeypatch, evaluate, tracker=None):
    db.scalars.return_value.all.return_value = bots
    db.scalar.side_effect = runs
    monkeypatch.setattr(bot_execution_loop, "evaluate_bot_on_latest_bar", evaluate)
    return bot_execution_loop.run_active_bots_once(db, tracker)


def _raise(exc: Exception):
    def evaluate(db_, bot, bot_run):
        raise exc

    return evaluate


def test_every_attempt_stamps_the_heartbeat_even_a_failed_one(monkeypatch) -> None:
    db = MagicMock()
    ok_bot, bad_bot = _bot(), _bot()
    runs = [_bot_run(bot_id=ok_bot.id), _bot_run(bot_id=bad_bot.id)]

    def evaluate(db_, bot, bot_run):
        if bot is bad_bot:
            raise RuntimeError("boom")
        return {"action": "hold"}

    _pass(db, [ok_bot, bad_bot], runs, monkeypatch, evaluate)

    stamped = [str(c.args[0]) for c in db.execute.call_args_list]
    assert len(stamped) == 2 and all("UPDATE fx.bot_run SET heartbeat_at" in s for s in stamped)
    assert db.commit.call_count == 2


def test_a_bot_that_keeps_failing_is_moved_to_failed_at_the_threshold(monkeypatch) -> None:
    bot = _bot()
    tracker = bot_execution_loop.EvaluationFailureTracker(threshold=3)
    failed = MagicMock()
    monkeypatch.setattr(bot_execution_loop.bot_lifecycle, "fail_bot", failed)

    for attempt in (1, 2, 3):
        db = MagicMock()
        db.get.return_value = bot
        _pass(db, [bot], [_bot_run(bot_id=bot.id)], monkeypatch, _raise(ValueError("bad")), tracker)
        assert failed.call_count == (1 if attempt == 3 else 0)

    failed.assert_called_once_with(db, bot, error_type="ValueError", consecutive_failures=3)
    assert tracker.count(bot.id) == 0  # a failed bot is no longer counted


def test_a_success_resets_the_count(monkeypatch) -> None:
    bot = _bot()
    tracker = bot_execution_loop.EvaluationFailureTracker(threshold=3)
    failed = MagicMock()
    monkeypatch.setattr(bot_execution_loop.bot_lifecycle, "fail_bot", failed)
    outcomes = iter([ValueError("x"), ValueError("x"), None, ValueError("x"), ValueError("x")])

    def evaluate(db_, bot_, run_):
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome
        return {"action": "hold"}

    for _ in range(5):
        db = MagicMock()
        db.get.return_value = bot
        _pass(db, [bot], [_bot_run(bot_id=bot.id)], monkeypatch, evaluate, tracker)

    failed.assert_not_called()  # never three in a row
    assert tracker.count(bot.id) == 2


def test_a_database_outage_is_not_the_bots_fault_and_is_not_counted(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    bot = _bot()
    tracker = bot_execution_loop.EvaluationFailureTracker(threshold=1)
    failed = MagicMock()
    monkeypatch.setattr(bot_execution_loop.bot_lifecycle, "fail_bot", failed)
    db = MagicMock()
    outage = OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    _pass(db, [bot], [_bot_run(bot_id=bot.id)], monkeypatch, _raise(outage), tracker)

    failed.assert_not_called()
    assert tracker.count(bot.id) == 0
    db.rollback.assert_called_once()


def test_failing_to_record_the_failure_is_retried_on_the_next_pass(monkeypatch) -> None:
    bot = _bot()
    tracker = bot_execution_loop.EvaluationFailureTracker(threshold=1)
    monkeypatch.setattr(
        bot_execution_loop.bot_lifecycle, "fail_bot", MagicMock(side_effect=RuntimeError("db"))
    )
    db = MagicMock()
    db.get.return_value = bot

    _pass(db, [bot], [_bot_run(bot_id=bot.id)], monkeypatch, _raise(ValueError("bad")), tracker)

    assert tracker.count(bot.id) == 1  # still counted: the next pass tries again
    assert db.rollback.call_count == 2  # the failed evaluation, then the failed recording


def test_a_bot_that_is_no_longer_active_is_not_failed(monkeypatch) -> None:
    """Stopped by someone between the failed evaluation and the recording."""
    bot = _bot(actual_state="stopped")
    tracker = bot_execution_loop.EvaluationFailureTracker(threshold=1)
    failed = MagicMock()
    monkeypatch.setattr(bot_execution_loop.bot_lifecycle, "fail_bot", failed)
    db = MagicMock()
    db.get.return_value = bot

    _pass(db, [bot], [_bot_run(bot_id=bot.id)], monkeypatch, _raise(ValueError("bad")), tracker)

    failed.assert_not_called()
