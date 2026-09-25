"""Standalone Bot execution Worker process (Horizon 3 execution loop --
docs/architecture-alignment-and-long-term-roadmap.md, 2026-09-25 "Bot管理API ->
実行ループ/Worker -> 最低限のUI"順, second step).

Run with `python -m app.trading.worker`. Mirrors
`app/notifications/worker/__main__.py`'s shape exactly (single process, no
lease/heartbeat machinery, poll interval from settings) -- see that module's
docstring and `app/trading/application/bot_execution_loop.py`'s module
docstring for why. Unlike the Notification Worker, this one has no external
configuration prerequisite to gate on (no SMTP-equivalent): it only needs the
normal `DATABASE_URL`, so it starts unconditionally and is included in
`scripts/start_local.py`.
"""

import asyncio
import contextlib
import logging
import signal
import sys

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.trading.application.bot_execution_loop import run_active_bots_once

logger = logging.getLogger(__name__)


async def _run(stop: asyncio.Event) -> None:
    while not stop.is_set():
        with SessionLocal() as db:
            evaluated = run_active_bots_once(db)
        if evaluated:
            logger.info("trading.worker: evaluated %d active bot(s)", evaluated)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                stop.wait(), timeout=settings.bot_execution_poll_interval_seconds
            )


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def handler(*_args: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    if sys.platform == "win32":
        for win_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            signal.signal(win_signal, handler)
    else:
        for posix_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(posix_signal, stop.set)


async def _main() -> None:
    stop = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    logger.info("trading.worker: starting")
    try:
        await _run(stop)
    finally:
        logger.info("trading.worker: stopped")


def main() -> int:
    configure_logging(settings, log_filename="trading_worker.log")
    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
