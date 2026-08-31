"""Execute one claimed page. No HTTP, scheduler, or process lifecycle responsibilities."""

import asyncio
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from app.market_data.infrastructure.leases import LeaseClaim, LeaseLost
from app.market_data.infrastructure.page_errors import classify_failure
from app.market_data.infrastructure.pages import PageStore

PageOutcome = Literal["saved", "completed", "failed", "discarded"]


class ExecuteMarketDataPage:
    def __init__(self, store: PageStore, *, timeout_seconds: float = 30) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be in (0, 30]")
        self.store = store
        self.timeout_seconds = timeout_seconds

    async def execute(self, claim: LeaseClaim) -> PageOutcome:
        try:
            page = self.store.prepare(claim)
            claim = page.claim
            if claim.work.kind == "backfill" and page.start == page.scan_end:
                self.store.complete(page)
                return "completed"
            points = await asyncio.wait_for(
                self.store.access.fetch(
                    page.access, claim.work.feed.timeframe, page.start, page.end
                ),
                timeout=self.timeout_seconds,
            )
            self.store.save(page, points)
            return "saved"
        except asyncio.CancelledError:
            self.store.leases.release(claim)
            raise
        except SQLAlchemyError:
            # An ambiguous commit must be recovered by a new claim, never retried with this token.
            raise
        except LeaseLost:
            self.store.leases.release(claim)
            return "discarded"
        except Exception as exc:
            try:
                self.store.fail(claim, classify_failure(exc))
            except LeaseLost:
                self.store.leases.release(claim)
                return "discarded"
            return "failed"
