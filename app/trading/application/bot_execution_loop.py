"""One evaluation pass over every currently active `TradingBot` (Horizon 3
execution loop -- docs/architecture/architecture-alignment-and-long-term-roadmap.md,
2026-09-25 "Bot管理API -> 実行ループ/Worker -> 最低限のUI"順, second step).

Calls `bot_evaluation.evaluate_bot_on_latest_bar` for every bot with
`actual_state` in `('running', 'paused')`, across every workspace -- a single
unscoped process, no lease/heartbeat machinery. This mirrors the Notification
Worker's shape (see `app/notifications/worker/__main__.py`'s docstring for the
same reasoning): each bot's evaluation is a handful of DB queries with no
external network I/O (paper execution only), not a long-running job that can
crash mid-flight and need stale-recovery, so the market-data worker's
lease/heartbeat design does not apply here either.

Safe to call on a poll interval faster than any bot's own candle interval
closes: `evaluate_bot_on_latest_bar` is idempotent per (bot_run_id, candle_id)
(see that function's own docstring) -- re-evaluating a still-latest candle is
a cheap "already_processed" no-op, not a duplicate signal or a crash.

**Failures (docs/plans/worker-failure-handling.md)**: one bot's failure never stops the pass,
but it no longer vanishes into a log line either.

- An evaluation that raises is rolled back whole (see `bot_evaluation`), so the next pass
  retries it. `EvaluationFailureTracker` counts consecutive failures per bot; at its threshold
  the bot is moved to `failed` (`bot_lifecycle.fail_bot`: stopped, orders cancelled, an
  `error` notification to the workspace's Owners and Operators). A success resets the count.
- A database outage (connection errors) is not the bot's fault and every bot would fail with
  it, so it is logged and not counted.
- Every attempt, successful or not, stamps `bot_run.heartbeat_at`: that is what
  `worker_watchdog` reads to notice a worker that has stopped.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from app.models.strategy import BotRun, TradingBot
from app.trading.application import bot_lifecycle
from app.trading.application.bot_evaluation import evaluate_bot_on_latest_bar

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 5
"""Consecutive failed passes before a bot is moved to `failed`: about 25 seconds at the default
5-second poll, long enough to ride out a glitch, short enough that a bot stuck on a bug does
not retry for hours."""


class EvaluationFailureTracker:
    """Consecutive evaluation failures per bot, kept in the worker's memory. A restarted
    worker starts counting again, which only delays moving a bot that really is stuck."""

    def __init__(self, threshold: int = FAILURE_THRESHOLD) -> None:
        self.threshold = threshold
        self._counts: dict[UUID, int] = {}

    def failed(self, bot_id: UUID) -> int:
        self._counts[bot_id] = self._counts.get(bot_id, 0) + 1
        return self._counts[bot_id]

    def succeeded(self, bot_id: UUID) -> None:
        self._counts.pop(bot_id, None)

    def count(self, bot_id: UUID) -> int:
        return self._counts.get(bot_id, 0)


def _is_database_outage(exc: Exception) -> bool:
    return isinstance(exc, OperationalError | InterfaceError)


def _stamp_heartbeat(db: Session, bot_run_id: UUID) -> None:
    try:
        db.execute(
            update(BotRun).where(BotRun.id == bot_run_id).values(heartbeat_at=datetime.now(UTC))
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("bot_execution_loop: heartbeat not stamped (%s)", type(exc).__name__)


def run_active_bots_once(db: Session, tracker: EvaluationFailureTracker | None = None) -> int:
    """Evaluates every active bot once. Returns the number of bots evaluated
    successfully (most cycles will resolve to "hold" or "already_processed", not a state
    change). A failure is rolled back, logged (exception type only, matching
    `market_data.worker.runner`'s convention of never logging the exception message itself)
    and counted by `tracker`; see the module docstring."""
    tracker = tracker or EvaluationFailureTracker()
    bots = db.scalars(
        select(TradingBot).where(TradingBot.actual_state.in_(("running", "paused")))
    ).all()
    evaluated = 0
    for bot in bots:
        bot_id = bot.id
        bot_run = db.scalar(
            select(BotRun).where(BotRun.bot_id == bot_id, BotRun.status.in_(("running", "paused")))
        )
        if bot_run is None:
            # bot_lifecycle.py's commands always keep actual_state and the active
            # BotRun in lockstep, so this should not happen; skip rather than
            # crash the whole pass if it ever does.
            logger.warning("bot_execution_loop: bot %s has no matching active BotRun", bot_id)
            continue
        bot_run_id = bot_run.id
        try:
            evaluate_bot_on_latest_bar(db, bot, bot_run)
        except Exception as exc:
            db.rollback()
            if _is_database_outage(exc):
                logger.warning("bot_execution_loop: database unavailable (%s)", type(exc).__name__)
                continue
            failures = tracker.failed(bot_id)
            logger.warning(
                "bot_execution_loop: bot %s evaluation failed (%s), %d in a row",
                bot_id,
                type(exc).__name__,
                failures,
            )
            _stamp_heartbeat(db, bot_run_id)
            if failures >= tracker.threshold:
                _fail_bot(db, bot_id, type(exc).__name__, failures, tracker)
            continue
        tracker.succeeded(bot_id)
        _stamp_heartbeat(db, bot_run_id)
        evaluated += 1
    return evaluated


def _fail_bot(
    db: Session, bot_id: UUID, error_type: str, failures: int, tracker: EvaluationFailureTracker
) -> None:
    try:
        bot = db.get(TradingBot, bot_id)
        if bot is not None and bot.actual_state in ("running", "paused"):
            bot_lifecycle.fail_bot(db, bot, error_type=error_type, consecutive_failures=failures)
        tracker.succeeded(bot_id)
    except Exception as exc:
        # Not recorded this time: the count stays, so the next pass tries again.
        db.rollback()
        logger.error(
            "bot_execution_loop: could not mark bot %s failed (%s)", bot_id, type(exc).__name__
        )
