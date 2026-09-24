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

**No real recipient targeting policy exists yet** -- `_no_recipients` below is
a placeholder `RecipientResolver` (see that module's docstring): with no
domain event producer writing to `outbox_event` yet, there is nothing to
target in the first place. Replace it once one exists; this loop, the SMTP
adapter, and the ORM are otherwise ready to use as-is.
"""

import asyncio
import contextlib
import logging
import signal
import sys

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models.audit import OutboxEvent, SystemEvent
from app.notifications.adapters.smtp import SmtpConfig, SmtpNotificationAdapter
from app.notifications.application.deliver_notifications import (
    RecipientResolver,
    deliver_pending_notifications,
)

logger = logging.getLogger(__name__)


def _require_smtp_configured() -> SmtpConfig:
    if settings.smtp_host is None or settings.smtp_sender_address is None:
        raise RuntimeError(
            "SMTP is not configured (SMTP_HOST/SMTP_SENDER_ADDRESS); set them in .env "
            "before starting the Notification Worker."
        )
    return SmtpConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=(settings.smtp_password.get_secret_value() if settings.smtp_password else None),
        use_tls=settings.smtp_use_tls,
        sender_address=settings.smtp_sender_address,
    )


def _no_recipients(_event: OutboxEvent, _system_event: SystemEvent) -> list[tuple[str, str]]:
    return []


async def _run(config: SmtpConfig, stop: asyncio.Event) -> None:
    adapter = SmtpNotificationAdapter(config)
    resolver: RecipientResolver = _no_recipients
    while not stop.is_set():
        with SessionLocal() as db:
            processed = deliver_pending_notifications(
                db, adapter=adapter, recipient_resolver=resolver
            )
        if processed:
            logger.info("notification.worker: processed %d outbox event(s)", processed)
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


async def _main(config: SmtpConfig) -> None:
    stop = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    logger.info("notification.worker: starting")
    try:
        await _run(config, stop)
    finally:
        logger.info("notification.worker: stopped")


def main() -> int:
    configure_logging(settings, log_filename="notification_worker.log")
    try:
        config = _require_smtp_configured()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    asyncio.run(_main(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
