"""Standalone Notification Worker process (Horizon5 Group D / Unit 8,
docs/plans/horizon5-implementation-plan.md).

Run with `python -m app.notifications.worker`. Deliberately does not use the
market-data worker's lease/heartbeat machinery: `claim_pending_outbox_events`'s
`SELECT ... FOR UPDATE SKIP LOCKED` already gives multiple worker processes
safe concurrent access to the same batch of outbox rows, and a notification
send is a single short call that either finishes or fails within one
transaction, not a long-running job that can crash mid-flight and need
stale-recovery (see `app/notifications/application/deliver_notifications.py`'s
module docstring).

Recipients are the workspace's Owners and Operators (`recipients.workspace_member_recipients`).
The `in_app` channel always works; `email` is added only when SMTP is configured
(SMTP_HOST/SMTP_SENDER_ADDRESS) -- without it the worker still runs and records in-app
notifications.
"""

import asyncio
import contextlib
import logging
import signal
import sys
import time
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.market_data.application.worker_watchdog import check_market_data_worker
from app.notifications.adapters.base import NotificationAdapter
from app.notifications.adapters.in_app import InAppNotificationAdapter
from app.notifications.adapters.smtp import SmtpConfig, SmtpNotificationAdapter
from app.notifications.application.deliver_notifications import deliver_pending_notifications
from app.notifications.application.recipients import workspace_member_recipients
from app.trading.application.worker_watchdog import check_trading_worker

logger = logging.getLogger(__name__)


def _smtp_config() -> SmtpConfig | None:
    if settings.smtp_host is None or settings.smtp_sender_address is None:
        return None
    return SmtpConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=(settings.smtp_password.get_secret_value() if settings.smtp_password else None),
        use_tls=settings.smtp_use_tls,
        sender_address=settings.smtp_sender_address,
    )


def _build_adapters(smtp: SmtpConfig | None) -> dict[str, NotificationAdapter]:
    adapters: dict[str, NotificationAdapter] = {"in_app": InAppNotificationAdapter()}
    if smtp is not None:
        adapters["email"] = SmtpNotificationAdapter(smtp)
    return adapters


_WATCHDOG_INTERVAL_SECONDS = 30.0


def _run_watchdog() -> None:
    """Each check on its own, so one failing does not skip the other."""
    checks = (
        (
            "trading worker",
            lambda db: check_trading_worker(
                db,
                now=datetime.now(UTC),
                stale_after=timedelta(seconds=settings.trading_worker_stale_seconds),
            ),
        ),
        (
            "market-data worker",
            lambda db: check_market_data_worker(
                db,
                now=datetime.now(UTC),
                overdue_after=timedelta(seconds=settings.market_data_worker_overdue_seconds),
            ),
        ),
    )
    for name, check in checks:
        try:
            with SessionLocal() as db:
                alerted = check(db)
            if alerted:
                logger.warning("notification.worker: %s stalled (%d workspace(s))", name, alerted)
        except Exception as exc:
            logger.warning("notification.worker: %s watchdog failed (%s)", name, type(exc).__name__)


async def _run(adapters: dict[str, NotificationAdapter], stop: asyncio.Event) -> None:
    next_watchdog = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_watchdog:
            _run_watchdog()
            next_watchdog = time.monotonic() + _WATCHDOG_INTERVAL_SECONDS
        try:
            with SessionLocal() as db:
                processed = deliver_pending_notifications(
                    db, adapters=adapters, recipient_resolver=workspace_member_recipients
                )
            if processed:
                logger.info("notification.worker: processed %d outbox event(s)", processed)
        except Exception as exc:
            # A database blip must not end the worker: the pending rows are still there.
            logger.warning("notification.worker: pass failed (%s)", type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.notification_poll_interval_seconds)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def handler(*_args: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    if sys.platform == "win32":
        for win_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            signal.signal(win_signal, handler)
    else:
        for posix_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(posix_signal, stop.set)


async def _main(adapters: dict[str, NotificationAdapter]) -> None:
    stop = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    logger.info("notification.worker: starting (channels: %s)", ", ".join(sorted(adapters)))
    try:
        await _run(adapters, stop)
    finally:
        logger.info("notification.worker: stopped")


def main() -> int:
    configure_logging(settings, log_filename="notification_worker.log")
    smtp = _smtp_config()
    if smtp is None:
        logger.warning("notification.worker: SMTP is not configured; email notifications are off")
    asyncio.run(_main(_build_adapters(smtp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
