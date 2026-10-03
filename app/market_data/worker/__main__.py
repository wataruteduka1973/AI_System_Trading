"""Standalone durable market-data worker process.

Run with `python -m app.market_data.worker`. Requires the database to already be at the
latest migration this code ships with; the API's lifespan no longer starts any collection.
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal, engine
from app.market_data.application.execute_page import ExecuteMarketDataPage
from app.market_data.infrastructure.candidates import CandidateScanner
from app.market_data.infrastructure.leases import LeaseStore
from app.market_data.infrastructure.normalize import normalize_legacy_jobs
from app.market_data.infrastructure.page_access import PageAccess
from app.market_data.infrastructure.pages import PageStore
from app.market_data.worker.runner import WorkerRunner
from app.security.secret_store import get_secret_store

logger = logging.getLogger(__name__)
MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[3] / "alembic"


def _required_revisions() -> frozenset[str]:
    """The migration head(s) this code ships with. The worker runs only against exactly
    this schema -- neither older nor newer -- and the requirement moves with every new
    migration. It used to be a hard-coded revision (`20260831_0005`), which made the
    worker refuse a correctly migrated database from the next migration on (2026-09-20
    to 2026-10-03, unnoticed because the launcher treats the worker as non-critical)."""
    return frozenset(ScriptDirectory(str(MIGRATIONS_DIRECTORY)).get_heads())


def _check_schema_revision() -> None:
    required = _required_revisions()
    with engine.connect() as connection:
        heads = frozenset(MigrationContext.configure(connection).get_current_heads())
    if heads != required:
        raise RuntimeError(
            "Database schema is at revision(s) "
            f"{sorted(heads) or ['<none>']}, but this code's migrations end at "
            f"{sorted(required)}. If the database is behind, run `alembic upgrade head`; "
            "if it is ahead, update this checkout. Then restart the worker."
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


def _build_runner() -> WorkerRunner:
    leases = LeaseStore(SessionLocal, lease_seconds=settings.worker_lease_seconds)
    access = PageAccess(get_secret_store())
    store = PageStore(leases, access)
    execute = ExecuteMarketDataPage(store, timeout_seconds=settings.worker_fetch_timeout_seconds)
    discover = CandidateScanner(SessionLocal).due
    return WorkerRunner(
        leases,
        execute,
        discover,
        scan_interval_seconds=settings.worker_scan_interval_seconds,
        heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
        recover_limit=settings.worker_recover_limit,
        candidate_limit=settings.worker_candidate_limit,
    )


async def _run() -> None:
    stop = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), stop)
    runner = _build_runner()
    logger.info("market_data.worker: starting (owner_id=%s)", runner.owner_id)
    try:
        await runner.run(stop)
    finally:
        logger.info("market_data.worker: stopped")


def _run_normalization() -> int:
    with SessionLocal() as db:
        result = normalize_legacy_jobs(db)
    print(
        f"Normalized {result.reset_count} legacy job(s); "
        f"skipped {result.skipped_already_migrated_count} already migrated."
    )
    return 0


def main() -> int:
    configure_logging(settings, log_filename="worker.log")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--normalize-legacy-jobs",
        action="store_true",
        help="Reset stale pre-cutover running/validating jobs to queued, then exit.",
    )
    args = parser.parse_args()
    try:
        _check_schema_revision()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if args.normalize_legacy_jobs:
        return _run_normalization()
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
