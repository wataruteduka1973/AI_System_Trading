"""Process-independent scheduling: fair round-robin over due work, with heartbeat keepalive.

No signal handling, engine/session construction, or CLI concerns belong here; see __main__.py.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from uuid import uuid4

from app.market_data.application.execute_page import ExecuteMarketDataPage
from app.market_data.infrastructure.leases import LeaseClaim, LeaseStore, WorkRef

logger = logging.getLogger(__name__)


class WorkerRunner:
    """One page per due feed per scan pass, so a long backfill never starves polling (DW-14)."""

    def __init__(
        self,
        leases: LeaseStore,
        execute: ExecuteMarketDataPage,
        discover: Callable[[int], list[WorkRef]],
        *,
        scan_interval_seconds: float = 2,
        heartbeat_interval_seconds: float = 15,
        recover_limit: int = 100,
        candidate_limit: int = 500,
    ) -> None:
        self.leases = leases
        self.execute = execute
        self.discover = discover
        self.scan_interval_seconds = scan_interval_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.recover_limit = recover_limit
        self.candidate_limit = candidate_limit
        self.owner_id = uuid4()

    async def run(self, stop: asyncio.Event, *, max_cycles: int | None = None) -> None:
        cycles = 0
        while not stop.is_set():
            await asyncio.to_thread(self.leases.recover_expired, self.recover_limit)
            candidates = await asyncio.to_thread(self.discover, self.candidate_limit)
            for work in candidates:
                if stop.is_set():
                    break
                await self._process(work)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            await self._wait(stop)

    async def _wait(self, stop: asyncio.Event) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=self.scan_interval_seconds)

    async def _process(self, work: WorkRef) -> None:
        try:
            claim = await asyncio.to_thread(self.leases.claim, work, self.owner_id)
            if claim is None:
                return
            await self._execute_with_heartbeat(claim)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # execute_page.py already normalizes SDK/access failures into safe outcomes; anything
            # reaching here (e.g. an ambiguous SQLAlchemyError) must not take down other feeds.
            # The lease's own expiry recovers the job; never log the exception message here.
            logger.warning(
                "market_data.worker: unrecovered failure on %s/%s (%s)",
                work.kind,
                work.feed.timeframe,
                type(exc).__name__,
            )

    async def _execute_with_heartbeat(self, claim: LeaseClaim) -> None:
        keepalive = asyncio.create_task(self._keepalive(claim))
        try:
            await self.execute.execute(claim)
        finally:
            keepalive.cancel()
            with suppress(asyncio.CancelledError):
                await keepalive

    async def _keepalive(self, claim: LeaseClaim) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            alive = await asyncio.to_thread(self.leases.heartbeat, claim)
            if not alive:
                return
