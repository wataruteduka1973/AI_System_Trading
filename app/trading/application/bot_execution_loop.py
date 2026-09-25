"""One evaluation pass over every currently active `TradingBot` (Horizon 3
execution loop -- docs/architecture-alignment-and-long-term-roadmap.md,
2026-09-25 "Bot管理API -> 実行ループ/Worker -> 最低限のUI"順, second step).

Calls `dummy_pipeline.run_dummy_pipeline_once` for every bot with
`actual_state` in `('running', 'paused')`, across every workspace -- a single
unscoped process, no lease/heartbeat machinery. This mirrors the Notification
Worker's shape (see `app/notifications/worker/__main__.py`'s docstring for the
same reasoning): each bot's evaluation is a handful of DB queries with no
external network I/O (paper execution only), not a long-running job that can
crash mid-flight and need stale-recovery, so the market-data worker's
lease/heartbeat design does not apply here either.

Safe to call on a poll interval faster than any bot's own candle interval
closes: `run_dummy_pipeline_once` is idempotent per (bot_run_id, candle_id)
(see that function's own docstring) -- re-evaluating a still-latest candle is
a cheap "already_processed" no-op, not a duplicate signal or a crash.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import BotRun, TradingBot
from app.trading.application.dummy_pipeline import run_dummy_pipeline_once

logger = logging.getLogger(__name__)


def run_active_bots_once(db: Session) -> int:
    """Evaluates every active bot once. Returns the number of bots evaluated
    (attempted -- most cycles will resolve to "hold" or "already_processed",
    not a state change). One bot's failure is caught, logged (exception type
    only, matching `market_data.worker.runner`'s convention of never logging
    the exception message itself), and rolled back so it cannot leave the
    shared session unusable for the remaining bots in this pass."""
    bots = db.scalars(
        select(TradingBot).where(TradingBot.actual_state.in_(("running", "paused")))
    ).all()
    evaluated = 0
    for bot in bots:
        bot_run = db.scalar(
            select(BotRun).where(BotRun.bot_id == bot.id, BotRun.status.in_(("running", "paused")))
        )
        if bot_run is None:
            # bot_lifecycle.py's commands always keep actual_state and the active
            # BotRun in lockstep, so this should not happen; skip rather than
            # crash the whole pass if it ever does.
            logger.warning("bot_execution_loop: bot %s has no matching active BotRun", bot.id)
            continue
        try:
            run_dummy_pipeline_once(db, bot, bot_run)
        except Exception as exc:
            db.rollback()
            logger.warning(
                "bot_execution_loop: bot %s evaluation failed (%s)", bot.id, type(exc).__name__
            )
            continue
        evaluated += 1
    return evaluated
