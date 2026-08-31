"""Fenced page preparation and persistence, isolated from legacy runtime wiring."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.exchanges.types import CandlePoint, timeframe_delta
from app.market_data.infrastructure.leases import LeaseClaim, LeaseStore
from app.market_data.infrastructure.models import WorkerBackfill, WorkerSubscription
from app.market_data.infrastructure.page_access import AccessSnapshot, PageAccess
from app.market_data.infrastructure.page_errors import PageFailure
from app.models.catalog import Candle
from app.services.market_data import (
    CandleIngestionService,
    IngestionReport,
    MarketDataAccessError,
    build_candle_coverage,
    persist_internal_gaps,
)


@dataclass(frozen=True)
class PreparedPage:
    claim: LeaseClaim
    access: AccessSnapshot
    start: datetime
    end: datetime
    scan_end: datetime
    observed_at: datetime


def final_points(page: PreparedPage, points: list[CandlePoint]) -> list[CandlePoint]:
    unique: dict[datetime, CandlePoint] = {}
    for point in points:
        if point.open_time.utcoffset() is None or point.close_time.utcoffset() is None:
            raise MarketDataAccessError("Invalid candle timestamps", "invalid_candles")
        # Providers can include their inclusive upper-bound candle. It is revisited.
        if not page.start <= point.open_time < page.end:
            continue
        if not point.is_final or point.close_time > min(page.end, page.observed_at):
            continue
        prices = (point.open, point.high, point.low, point.close)
        if (
            point.close_time <= point.open_time
            or not all(value.is_finite() and value > 0 for value in prices)
            or not point.low
            <= min(point.open, point.close)
            <= max(point.open, point.close)
            <= point.high
            or (point.volume is not None and (not point.volume.is_finite() or point.volume < 0))
            or (point.trade_count is not None and point.trade_count < 0)
            or (point.open_time in unique and unique[point.open_time] != point)
        ):
            raise MarketDataAccessError("Invalid candle values", "invalid_candles")
        unique[point.open_time] = point
    return sorted(unique.values(), key=lambda point: point.open_time)


def restore_report(target: WorkerBackfill) -> IngestionReport:
    report = IngestionReport(target.from_time, target.to_time)
    saved = target.progress_report or {}
    for name in (
        "source_rows_received",
        "rows_inserted",
        "rows_updated",
        "empty_source_window_count",
    ):
        value = saved.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise MarketDataAccessError("Invalid report counter", "invalid_checkpoint")
        setattr(report, name, value)
    for name in ("actual_first_candle_time", "actual_last_candle_time"):
        value = saved.get(name)
        if isinstance(value, str):
            setattr(report, name, datetime.fromisoformat(value))
    samples = saved.get("empty_source_window_samples", [])
    if not isinstance(samples, list):
        raise MarketDataAccessError("Invalid report samples", "invalid_checkpoint")
    for sample in samples[:20]:
        if not isinstance(sample, dict):
            raise MarketDataAccessError("Invalid report sample", "invalid_checkpoint")
        start, end = sample.get("from_time"), sample.get("to_time")
        if not isinstance(start, str) or not isinstance(end, str):
            raise MarketDataAccessError("Invalid report sample times", "invalid_checkpoint")
        report.empty_source_window_samples.append({"from_time": start, "to_time": end})
    return report


class PageStore:
    def __init__(
        self, leases: LeaseStore, access: PageAccess, *, page_size: int | None = None
    ) -> None:
        if page_size is not None and not 2 <= page_size <= 950:
            raise ValueError("page_size override must be between 2 and 950")
        self.leases = leases
        self.access = access
        self.page_size = page_size

    def prepare(self, claim: LeaseClaim) -> PreparedPage:
        with self.leases.guarded_write(claim, release=False) as (db, target):
            access = self.access.resolve(db, claim.work.feed)
            delta = timeframe_delta(claim.work.feed.timeframe)
            now = self.leases._now(db)
            overlap = True
            if isinstance(target, WorkerSubscription):
                if target.scan_to is None and target.next_fetch_at is None:
                    latest = db.scalar(
                        select(func.max(Candle.close_time)).where(
                            Candle.instrument_id == target.instrument_id,
                            Candle.timeframe == target.timeframe,
                            Candle.is_final.is_(True),
                        )
                    )
                    target.scan_to = now
                    target.next_fetch_at = min(latest, now) if latest else now - 2 * delta
                    overlap = latest is not None
                if target.scan_to is None or target.next_fetch_at is None:
                    raise MarketDataAccessError("Invalid checkpoint", "invalid_checkpoint")
                start, scan_end = target.next_fetch_at, target.scan_to
            else:
                start = target.next_fetch_at or target.from_time
                scan_end = target.to_time
                if not target.from_time <= start <= scan_end:
                    raise MarketDataAccessError("Invalid checkpoint", "invalid_checkpoint")
            updated = replace(claim, next_fetch_at=target.next_fetch_at)
            size = self.page_size or (4900 if access.exchange == "oanda" else 950)
            # Include the overlap in the provider's page budget, guaranteeing progress.
            request_start = start
            if isinstance(target, WorkerBackfill):
                overlap = target.from_time < start < scan_end
            if overlap:
                request_start -= delta
                if isinstance(target, WorkerBackfill):
                    request_start = max(request_start, target.from_time)
            end = min(scan_end, request_start + size * delta)
            return PreparedPage(updated, access, request_start, end, scan_end, now)

    def save(self, page: PreparedPage, points: list[CandlePoint]) -> None:
        accepted = final_points(page, points)
        with self.leases.guarded_write(page.claim) as (db, target):
            self._recheck(db, page)
            inserted = updated = 0
            if accepted:
                inserted, updated = CandleIngestionService(db, self.access.secrets)._upsert_points(
                    target.instrument_id,
                    target.timeframe,
                    page.access.exchange,
                    "backfilled" if isinstance(target, WorkerBackfill) else "complete",
                    accepted,
                )
            target.next_fetch_at = page.end
            target.consecutive_failures = 0
            now = self.leases._now(db)
            target.next_run_at = now
            if isinstance(target, WorkerBackfill):
                report = restore_report(target)
                report.source_rows_received += len(points)
                report.rows_inserted += inserted
                report.rows_updated += updated
                report.record_candles(accepted)
                if not points:
                    report.record_empty_window(page.start, page.end)
                target.progress_report = report.as_validation_result({}, [])
                target.rows_written = report.rows_written
                target.error_code = None
            else:
                target.last_polled_at = now
                target.last_error_code = None
                if page.end == page.scan_end:
                    target.next_fetch_at = target.scan_to = None
                    target.last_success_at = now
                    target.next_run_at = now + timedelta(seconds=target.poll_interval_seconds)

    def complete(self, page: PreparedPage) -> None:
        with self.leases.guarded_write(page.claim) as (db, target):
            self._recheck(db, page)
            if not isinstance(target, WorkerBackfill):
                raise MarketDataAccessError("Invalid checkpoint", "invalid_checkpoint")
            gaps = persist_internal_gaps(
                db,
                instrument_id=target.instrument_id,
                timeframe=target.timeframe,
                requested_from=target.from_time,
                requested_to=target.to_time,
            )
            coverage = build_candle_coverage(
                db, target.instrument_id, target.timeframe, target.from_time, target.to_time
            )
            before = self.leases._state(target)
            target.validation_result = restore_report(target).as_validation_result(coverage, gaps)
            target.status = "succeeded"
            target.error_code = None
            target.consecutive_failures = 0
            target.finished_at = self.leases._now(db)
            self.leases._audit(
                db,
                page.claim.work,
                "market_data.work_completed",
                self.leases._state(target),
                before,
            )

    def fail(self, claim: LeaseClaim, failure: PageFailure) -> None:
        with self.leases.guarded_write(claim) as (db, target):
            before = self.leases._state(target)
            now = self.leases._now(db)
            target.consecutive_failures += 1
            retry = failure.retryable and target.consecutive_failures < 3
            delay = max(30 if target.consecutive_failures == 1 else 120, failure.retry_after)
            target.next_run_at = now + timedelta(seconds=delay + claim.work.id.int % 6)
            if isinstance(target, WorkerBackfill):
                target.error_code = failure.code
                target.status = "queued" if retry else "failed"
                target.finished_at = None if retry else now
            else:
                target.last_error_code = failure.code
                target.last_polled_at = now
                if not retry:
                    target.blocked_reason = failure.code
            self.leases._audit(
                db,
                claim.work,
                "market_data.work_retry" if retry else "market_data.work_blocked",
                {**self.leases._state(target), "error_code": failure.code},
                before,
            )

    def _recheck(self, db: Session, page: PreparedPage) -> None:
        if self.access.resolve(db, page.claim.work.feed) != page.access:
            raise MarketDataAccessError("Access changed during request", "access_changed")
