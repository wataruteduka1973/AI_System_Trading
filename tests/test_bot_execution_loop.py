"""`run_active_bots_once` (execution loop/Worker task,
docs/architecture-alignment-and-long-term-roadmap.md 2026-09-25 "Bot管理API ->
実行ループ/Worker -> 最低限のUI"順). Covers bot discovery, the missing-BotRun
skip path, and that one bot's failure does not stop the rest of the batch --
`run_dummy_pipeline_once` itself is covered separately in
tests/test_dummy_pipeline.py.
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
        "run_dummy_pipeline_once",
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
    monkeypatch.setattr(bot_execution_loop, "run_dummy_pipeline_once", called)

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

    monkeypatch.setattr(bot_execution_loop, "run_dummy_pipeline_once", fake_run)

    evaluated = bot_execution_loop.run_active_bots_once(db)

    assert evaluated == 1  # only the healthy bot counted
    db.rollback.assert_called_once()
