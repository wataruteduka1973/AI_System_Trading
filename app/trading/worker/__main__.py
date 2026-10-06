"""Standalone Bot execution Worker process (Horizon 3 execution loop --
docs/architecture/architecture-alignment-and-long-term-roadmap.md, 2026-09-25 "Bot管理API ->
実行ループ/Worker -> 最低限のUI"順, second step).

Run with `python -m app.trading.worker`. Mirrors
`app/notifications/worker/__main__.py`'s shape exactly (single process, no
lease/heartbeat machinery, poll interval from settings) -- see that module's
docstring and `app/trading/application/bot_execution_loop.py`'s module
docstring for why. Unlike the Notification Worker, this one has no external
configuration prerequisite to gate on (no SMTP-equivalent): it only needs the
normal `DATABASE_URL`, so it starts unconditionally and is included in
`scripts/start_local.py`.

Before each pass it refreshes the public production prices that paper-trading
bots on `binance_public` instruments need (docs/plans/paper-trading-live-data.md
Unit 1) -- read-only, keyless requests; no order is ever sent anywhere.
"""

import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.exchanges.binance_public import BinancePublicClient, get_binance_public_client
from app.trading.application.bot_execution_loop import run_active_bots_once
from app.trading.application.public_price_refresh import (
    refresh_public_prices_for_active_bots,
    refresh_public_spreads_for_active_bots,
)

logger = logging.getLogger(__name__)


async def _refresh_public_prices(db: Session, client: BinancePublicClient) -> None:
    """Never lets a pricing problem stop the evaluation pass: a bot without a
    fresh candle just has nothing new to evaluate this time."""
    try:
        await refresh_public_prices_for_active_bots(db, client, now=datetime.now(UTC))
    except Exception as exc:
        db.rollback()
        logger.warning("trading.worker: public price refresh failed (%s)", type(exc).__name__)
    try:
        await refresh_public_spreads_for_active_bots(db, client, now=datetime.now(UTC))
    except Exception as exc:
        db.rollback()
        logger.warning("trading.worker: public spread refresh failed (%s)", type(exc).__name__)


async def _run(stop: asyncio.Event) -> None:
    client = get_binance_public_client()
    while not stop.is_set():
        with SessionLocal() as db:
            await _refresh_public_prices(db, client)
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
