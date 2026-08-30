import asyncio
import hashlib
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.exchanges.binance import (
    BinanceApiError,
    BinanceAuthenticationError,
    BinanceSpotTestnetClient,
)
from app.exchanges.oanda import OandaApiError, OandaAuthenticationError, OandaPracticeClient
from app.exchanges.types import CandlePoint, timeframe_delta
from app.models.catalog import (
    AuditLog,
    BackfillJob,
    Candle,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    MarketDataGap,
    MarketDataSubscription,
    WorkspaceAccountSelection,
)
from app.services.secrets import LocalEncryptedSecretStore, get_secret_store


class MarketDataAccessError(RuntimeError):
    def __init__(self, message: str, code: str = "configuration_error") -> None:
        super().__init__(message)
        self.code = code


class DuplicateBackfillError(RuntimeError):
    pass


@dataclass(frozen=True)
class GapWindow:
    from_time: datetime
    to_time: datetime
    expected_count: int
    missing_count: int
    reason_code: str = "internal_missing_candles"

    def as_dict(self) -> dict[str, object]:
        values = asdict(self)
        values["from_time"] = self.from_time.isoformat()
        values["to_time"] = self.to_time.isoformat()
        return values


@dataclass
class IngestionReport:
    requested_from: datetime
    requested_to: datetime
    actual_first_candle_time: datetime | None = None
    actual_last_candle_time: datetime | None = None
    source_rows_received: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    empty_source_window_count: int = 0
    empty_source_window_samples: list[dict[str, str]] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        return self.rows_inserted + self.rows_updated

    def record_empty_window(self, start: datetime, end: datetime) -> None:
        self.empty_source_window_count += 1
        if len(self.empty_source_window_samples) < 20:
            self.empty_source_window_samples.append(
                {"from_time": start.isoformat(), "to_time": end.isoformat()}
            )

    def record_candles(self, points: list[CandlePoint]) -> None:
        if not points:
            return
        first = min(point.open_time for point in points)
        last = max(point.open_time for point in points)
        if self.actual_first_candle_time is None or first < self.actual_first_candle_time:
            self.actual_first_candle_time = first
        if self.actual_last_candle_time is None or last > self.actual_last_candle_time:
            self.actual_last_candle_time = last

    def as_validation_result(
        self, coverage: dict[str, object], gaps: list[GapWindow]
    ) -> dict[str, object]:
        coverage_status = coverage.get("coverage_status")
        safe_reason_code = coverage.get("source_limitation")
        if safe_reason_code is None and coverage_status == "partial_gaps":
            safe_reason_code = "internal_missing_candles"
        elif safe_reason_code is None and coverage_status == "empty":
            safe_reason_code = "empty_source_response"
        return {
            "requested_from": self.requested_from.isoformat(),
            "requested_to": self.requested_to.isoformat(),
            "actual_first_candle_time": (
                self.actual_first_candle_time.isoformat() if self.actual_first_candle_time else None
            ),
            "actual_last_candle_time": (
                self.actual_last_candle_time.isoformat() if self.actual_last_candle_time else None
            ),
            "source_rows_received": self.source_rows_received,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "rows_written": self.rows_written,
            "empty_source_window_count": self.empty_source_window_count,
            "empty_source_window_samples": self.empty_source_window_samples,
            "final_candles_only": True,
            "duplicates": "upserted",
            "internal_gap_count": len(gaps),
            "internal_gap_samples": [gap.as_dict() for gap in gaps[:20]],
            "safe_reason_code": safe_reason_code,
            **coverage,
        }


def ensure_no_overlapping_backfill(
    db: Session,
    *,
    workspace_id: UUID,
    instrument_id: UUID,
    timeframe: str,
    requested_from: datetime,
    requested_to: datetime,
) -> None:
    lock_key = _advisory_lock_key("backfill", workspace_id, instrument_id, timeframe)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    recover_interrupted_backfills(
        db, workspace_id, instrument_id, timeframe, requested_from, requested_to
    )
    duplicate_id = db.scalar(
        select(BackfillJob.id).where(
            BackfillJob.workspace_id == workspace_id,
            BackfillJob.instrument_id == instrument_id,
            BackfillJob.timeframe == timeframe,
            BackfillJob.status.in_(("queued", "running")),
            BackfillJob.from_time < requested_to,
            BackfillJob.to_time > requested_from,
        )
    )
    if duplicate_id is not None:
        raise DuplicateBackfillError("An overlapping backfill is already queued or running")


def recover_interrupted_backfills(
    db: Session,
    workspace_id: UUID,
    instrument_id: UUID,
    timeframe: str,
    requested_from: datetime,
    requested_to: datetime,
) -> None:
    """Recover old unowned jobs under the caller's overlap-serialization lock."""
    now = datetime.now(UTC)
    candidates = db.scalars(
        select(BackfillJob)
        .where(
            BackfillJob.workspace_id == workspace_id,
            BackfillJob.instrument_id == instrument_id,
            BackfillJob.timeframe == timeframe,
            BackfillJob.status.in_(("queued", "running")),
            func.coalesce(BackfillJob.started_at, BackfillJob.created_at)
            < now - timedelta(minutes=5),
            BackfillJob.from_time < requested_to,
            BackfillJob.to_time > requested_from,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in candidates:
        key = _advisory_lock_key("backfill-owner", job.id)
        if not db.scalar(select(func.pg_try_advisory_xact_lock(key))):
            continue
        previous_status = job.status
        job.status = "failed"
        job.error_code = "worker_interrupted"
        job.finished_at = now
        db.add(
            AuditLog(
                workspace_id=workspace_id,
                action="candle.backfill_recovered",
                resource_type="backfill_job",
                resource_id=job.id,
                correlation_id=uuid4(),
                before_data={"status": previous_status},
                after_data={"status": "failed", "error_code": "worker_interrupted"},
            )
        )
    db.flush()


def find_internal_gaps(
    open_times: list[datetime], timeframe: str, exchange_code: str
) -> list[GapWindow]:
    delta = timeframe_delta(timeframe)
    ordered = sorted(set(open_times))
    gaps: list[GapWindow] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        missing_times: list[datetime] = []
        candidate = previous + delta
        while candidate < current:
            if _is_expected_market_time(exchange_code, candidate):
                missing_times.append(candidate)
            candidate += delta
        if not missing_times:
            continue
        segment_start = missing_times[0]
        segment_count = 1
        for prior_missing, missing in zip(missing_times, missing_times[1:], strict=False):
            if missing != prior_missing + delta:
                gaps.append(
                    GapWindow(
                        from_time=segment_start,
                        to_time=prior_missing + delta,
                        expected_count=segment_count,
                        missing_count=segment_count,
                    )
                )
                segment_start = missing
                segment_count = 1
            else:
                segment_count += 1
        gaps.append(
            GapWindow(
                from_time=segment_start,
                to_time=missing_times[-1] + delta,
                expected_count=segment_count,
                missing_count=segment_count,
            )
        )
    return gaps


def persist_internal_gaps(
    db: Session,
    *,
    instrument_id: UUID,
    timeframe: str,
    requested_from: datetime,
    requested_to: datetime,
) -> list[GapWindow]:
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                _advisory_lock_key("market-data-gap", instrument_id, timeframe)
            )
        )
    )
    exchange_code = db.scalar(
        select(Exchange.code)
        .join(Instrument, Instrument.exchange_id == Exchange.id)
        .where(Instrument.id == instrument_id)
    )
    open_times = list(
        db.scalars(
            select(Candle.open_time)
            .where(
                Candle.instrument_id == instrument_id,
                Candle.timeframe == timeframe,
                Candle.is_final.is_(True),
                Candle.open_time >= requested_from,
                Candle.open_time < requested_to,
            )
            .order_by(Candle.open_time)
        ).all()
    )
    gaps = find_internal_gaps(open_times, timeframe, exchange_code or "unknown")
    existing = list(
        db.scalars(
            select(MarketDataGap).where(
                MarketDataGap.instrument_id == instrument_id,
                MarketDataGap.timeframe == timeframe,
                MarketDataGap.reason_code == "internal_missing_candles",
                MarketDataGap.status == "open",
                MarketDataGap.from_time < requested_to,
                MarketDataGap.to_time > requested_from,
            )
        ).all()
    )
    gaps_by_key = {(gap.from_time, gap.to_time): gap for gap in gaps}
    existing_by_key = {(gap.from_time, gap.to_time): gap for gap in existing}
    stored_open_times = set(open_times)
    now = datetime.now(UTC)
    for key, existing_gap in existing_by_key.items():
        overlapping_replacement = any(
            gap.from_time < existing_gap.to_time and gap.to_time > existing_gap.from_time
            for gap in gaps
        )
        if key not in gaps_by_key and (
            overlapping_replacement
            or _gap_is_filled(
                existing_gap, stored_open_times, timeframe, exchange_code or "unknown"
            )
        ):
            existing_gap.status = "resolved"
            existing_gap.resolved_at = now
    for key, gap in gaps_by_key.items():
        existing_gap = existing_by_key.get(key)
        if existing_gap is not None:
            existing_gap.expected_count = gap.expected_count
            existing_gap.missing_count = gap.missing_count
            continue
        db.add(
            MarketDataGap(
                instrument_id=instrument_id,
                timeframe=timeframe,
                from_time=gap.from_time,
                to_time=gap.to_time,
                expected_count=gap.expected_count,
                missing_count=gap.missing_count,
                reason_code=gap.reason_code,
                status="open",
            )
        )
    return gaps


def _is_expected_market_time(exchange_code: str, candle_open_time: datetime) -> bool:
    if exchange_code != "oanda":
        return True
    new_york_time = candle_open_time.astimezone(ZoneInfo("America/New_York"))
    weekday = new_york_time.weekday()
    if weekday == 5:
        return False
    if weekday == 4 and new_york_time.hour >= 17:
        return False
    return not (weekday == 6 and new_york_time.hour < 17)


def _gap_is_filled(
    gap: MarketDataGap,
    stored_open_times: set[datetime],
    timeframe: str,
    exchange_code: str,
) -> bool:
    delta = timeframe_delta(timeframe)
    candidate = gap.from_time
    expected = 0
    while candidate < gap.to_time:
        if _is_expected_market_time(exchange_code, candidate):
            expected += 1
            if candidate not in stored_open_times:
                return False
        candidate += delta
    return expected > 0


def _advisory_lock_key(namespace: str, *parts: object) -> int:
    lock_material = ":".join((namespace, *(str(part) for part in parts))).encode()
    return int.from_bytes(hashlib.blake2b(lock_material, digest_size=8).digest(), signed=True)


def classify_candle_coverage(
    *,
    exchange_code: str | None,
    timeframe: str,
    requested_from: datetime | None,
    requested_to: datetime | None,
    stored_count: int,
    actual_from: datetime | None,
    actual_to: datetime | None,
    internal_missing_count: int | None = None,
) -> dict[str, object]:
    delta = timeframe_delta(timeframe)
    expected_count: int | None = None
    missing_count: int | None = None
    coverage_status = "empty"
    if stored_count and actual_from is not None and actual_to is not None:
        missing_count = internal_missing_count
        if exchange_code == "binance":
            if requested_from is not None and requested_to is not None:
                expected_count = max(0, int((requested_to - requested_from) / delta))
            if missing_count is None:
                actual_expected = max(1, int((actual_to - actual_from) / delta))
                missing_count = max(0, actual_expected - stored_count)
        starts_in_range = requested_from is None or actual_from <= requested_from + delta
        ends_in_range = requested_to is None or actual_to >= requested_to - delta
        if starts_in_range and ends_in_range and (missing_count in {None, 0}):
            coverage_status = "complete"
        elif missing_count not in {None, 0}:
            coverage_status = "partial_gaps"
        else:
            coverage_status = "partial_source_limit"
    return {
        "requested_from": requested_from.isoformat() if requested_from else None,
        "requested_to": requested_to.isoformat() if requested_to else None,
        "actual_from": actual_from.isoformat() if actual_from else None,
        "actual_to": actual_to.isoformat() if actual_to else None,
        "stored_count": stored_count,
        "expected_count": expected_count,
        "missing_count": missing_count,
        "coverage_status": coverage_status,
        "source_limitation": (
            "binance_testnet_periodic_reset"
            if exchange_code == "binance" and coverage_status == "partial_source_limit"
            else None
        ),
    }


def build_candle_coverage(
    db: Session,
    instrument_id: UUID,
    timeframe: str,
    requested_from: datetime | None,
    requested_to: datetime | None,
) -> dict[str, object]:
    filters = [
        Candle.instrument_id == instrument_id,
        Candle.timeframe == timeframe,
        Candle.is_final.is_(True),
    ]
    if requested_from is not None:
        filters.append(Candle.open_time >= requested_from)
    if requested_to is not None:
        filters.append(Candle.open_time < requested_to)
    stored_count, actual_from, actual_to = db.execute(
        select(
            func.count(Candle.id),
            func.min(Candle.open_time),
            func.max(Candle.close_time),
        ).where(*filters)
    ).one()
    exchange_code = db.scalar(
        select(Exchange.code)
        .join(Instrument, Instrument.exchange_id == Exchange.id)
        .where(Instrument.id == instrument_id)
    )
    open_times = list(
        db.scalars(select(Candle.open_time).where(*filters).order_by(Candle.open_time)).all()
    )
    gaps = find_internal_gaps(open_times, timeframe, exchange_code or "unknown")
    return classify_candle_coverage(
        exchange_code=exchange_code,
        timeframe=timeframe,
        requested_from=requested_from,
        requested_to=requested_to,
        stored_count=stored_count,
        actual_from=actual_from,
        actual_to=actual_to,
        internal_missing_count=sum(gap.missing_count for gap in gaps),
    )


class CandleIngestionService:
    def __init__(
        self,
        db: Session,
        secret_store: LocalEncryptedSecretStore,
        oanda_client: OandaPracticeClient | None = None,
        binance_client: BinanceSpotTestnetClient | None = None,
    ) -> None:
        self.db = db
        self.secret_store = secret_store
        self.oanda_client = oanda_client or OandaPracticeClient()
        self.binance_client = binance_client or BinanceSpotTestnetClient()

    def validate_configuration(self, workspace_id: UUID, instrument_id: UUID) -> None:
        """Check local access and decryptability without sending any exchange request."""
        _instrument, exchange, connection = self._resolve_access(workspace_id, instrument_id)
        credentials = self._load_credentials(connection)
        required = ("token",) if exchange.code == "oanda" else ("api_key", "secret_key")
        if not all(credentials.get(key) for key in required):
            raise MarketDataAccessError(
                "Exchange credentials are incomplete", "credentials_missing"
            )

    async def sync(
        self,
        workspace_id: UUID,
        instrument_id: UUID,
        timeframe: str,
        start: datetime,
        end: datetime,
        quality_status: str,
    ) -> IngestionReport:
        instrument, exchange, connection = self._resolve_access(workspace_id, instrument_id)
        credentials = self._load_credentials(connection)
        cursor = start.astimezone(UTC)
        end = end.astimezone(UTC)
        page_size = 4900 if exchange.code == "oanda" else 950
        delta = timeframe_delta(timeframe)
        report = IngestionReport(requested_from=cursor, requested_to=end)
        while cursor < end:
            page_end = min(end, cursor + delta * page_size)
            points = await self._fetch_page(
                exchange.code,
                connection,
                credentials,
                instrument.symbol,
                timeframe,
                cursor,
                page_end,
            )
            report.source_rows_received += len(points)
            if not points:
                report.record_empty_window(cursor, page_end)
            final_points = [point for point in points if point.is_final and point.close_time <= end]
            if final_points:
                report.record_candles(final_points)
                inserted, updated = self._upsert_points(
                    instrument.id, timeframe, exchange.code, quality_status, final_points
                )
                report.rows_inserted += inserted
                report.rows_updated += updated
            cursor = page_end
        return report

    def latest_close_time(self, instrument_id: UUID, timeframe: str) -> datetime | None:
        return self.db.scalar(
            select(Candle.close_time)
            .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe)
            .order_by(Candle.close_time.desc())
            .limit(1)
        )

    def _resolve_access(
        self, workspace_id: UUID, instrument_id: UUID
    ) -> tuple[Instrument, Exchange, ExchangeConnection]:
        row = self.db.execute(
            select(Instrument, Exchange, ExchangeConnection)
            .join(Exchange, Instrument.exchange_id == Exchange.id)
            .join(
                WorkspaceAccountSelection,
                (WorkspaceAccountSelection.workspace_id == workspace_id)
                & (WorkspaceAccountSelection.exchange_id == Exchange.id),
            )
            .join(
                ExternalAccount,
                ExternalAccount.id == WorkspaceAccountSelection.external_account_id,
            )
            .join(ExchangeConnection, ExternalAccount.connection_id == ExchangeConnection.id)
            .where(
                Instrument.id == instrument_id,
                ExchangeConnection.workspace_id == workspace_id,
                ExchangeConnection.exchange_id == Exchange.id,
                ExchangeConnection.status == "verified",
                ExternalAccount.status == "active",
            )
        ).one_or_none()
        if row is None:
            raise MarketDataAccessError(
                "Instrument requires a selected active account from a verified connection"
            )
        return row

    def _load_credentials(self, connection: ExchangeConnection) -> dict[str, str]:
        if not connection.secret_ref:
            raise MarketDataAccessError(
                "Selected connection credentials are missing", "credentials_missing"
            )
        try:
            return self.secret_store.get(connection.secret_ref)
        except (KeyError, ValueError, OSError) as exc:
            raise MarketDataAccessError(
                "Selected connection credentials cannot be loaded", "credentials_unreadable"
            ) from exc

    async def _fetch_page(
        self,
        exchange_code: str,
        connection: ExchangeConnection,
        credentials: dict[str, str],
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[CandlePoint]:
        if exchange_code == "oanda":
            token = credentials.get("token")
            if not token:
                raise MarketDataAccessError("OANDA token is missing")
            return await self.oanda_client.get_candles(
                connection.api_base_url, token, symbol, timeframe, start, end
            )
        api_key = credentials.get("api_key")
        secret_key = credentials.get("secret_key")
        if not api_key or not secret_key:
            raise MarketDataAccessError("Binance API credentials are missing")
        return await self.binance_client.get_candles(
            connection.api_base_url,
            api_key,
            secret_key,
            symbol,
            timeframe,
            start,
            end,
        )

    def _upsert_points(
        self,
        instrument_id: UUID,
        timeframe: str,
        source: str,
        quality_status: str,
        points: list[CandlePoint],
    ) -> tuple[int, int]:
        received_at = datetime.now(UTC)
        values = [
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "open_time": point.open_time,
                "close_time": point.close_time,
                "open": point.open,
                "high": point.high,
                "low": point.low,
                "close": point.close,
                "volume": point.volume,
                "trade_count": point.trade_count,
                "source": source,
                "quality_status": quality_status,
                "is_final": True,
                "received_at": received_at,
            }
            for point in points
        ]
        statement = pg_insert(Candle).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_candle_business_key",
            set_={
                "close_time": statement.excluded.close_time,
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "trade_count": statement.excluded.trade_count,
                "source": statement.excluded.source,
                "quality_status": statement.excluded.quality_status,
                "is_final": True,
                "received_at": received_at,
                "corrected_at": received_at,
            },
        ).returning(literal_column("xmax = 0"))
        inserted_flags = list(self.db.scalars(statement).all())
        inserted = sum(bool(flag) for flag in inserted_flags)
        return inserted, len(inserted_flags) - inserted


def market_data_error_code(exc: Exception) -> str:
    if isinstance(exc, (OandaAuthenticationError, BinanceAuthenticationError)):
        return "authentication_failed"
    if isinstance(exc, (OandaApiError, BinanceApiError)):
        return "communication_failed"
    if isinstance(exc, MarketDataAccessError):
        return exc.code
    if isinstance(exc, HTTPException):
        return "configuration_error"
    return "internal_error"


async def run_backfill_job(job_id: UUID) -> None:
    # Pin the owning connection: a pooled session may switch connections on commit.
    key = _advisory_lock_key("backfill-owner", job_id)
    with engine.connect() as owner:
        if not owner.scalar(select(func.pg_try_advisory_lock(key))):
            return
        try:
            await _run_owned_backfill_job(job_id)
        finally:
            owner.execute(select(func.pg_advisory_unlock(key)))


async def _run_owned_backfill_job(job_id: UUID) -> None:
    with SessionLocal() as db:
        job = db.get(BackfillJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.attempts += 1
        job.started_at = datetime.now(UTC)
        db.commit()
        try:
            secret_store = get_secret_store()
            service = CandleIngestionService(db, secret_store)
            report = await service.sync(
                job.workspace_id,
                job.instrument_id,
                job.timeframe,
                job.from_time,
                job.to_time,
                "backfilled",
            )
            job.rows_written = report.rows_written
            gaps = persist_internal_gaps(
                db,
                instrument_id=job.instrument_id,
                timeframe=job.timeframe,
                requested_from=job.from_time,
                requested_to=job.to_time,
            )
            job.status = "succeeded"
            coverage = build_candle_coverage(
                db,
                job.instrument_id,
                job.timeframe,
                job.from_time,
                job.to_time,
            )
            job.validation_result = report.as_validation_result(coverage, gaps)
            job.error_code = None
        except Exception as exc:
            db.rollback()
            job = db.get(BackfillJob, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error_code = market_data_error_code(exc)
        job.finished_at = datetime.now(UTC)
        db.commit()


class MarketDataPollingWorker:
    def __init__(self, scan_interval_seconds: int = 60) -> None:
        self.scan_interval_seconds = scan_interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.scan_interval_seconds)
            await self.poll_once()

    async def poll_once(self) -> None:
        with SessionLocal() as db:
            now = datetime.now(UTC)
            subscriptions = list(
                db.scalars(
                    select(MarketDataSubscription).where(MarketDataSubscription.enabled.is_(True))
                ).all()
            )
            for subscription in subscriptions:
                if (
                    subscription.last_polled_at is not None
                    and subscription.last_polled_at
                    + timedelta(seconds=subscription.poll_interval_seconds)
                    > now
                ):
                    continue
                await self._poll_subscription(db, subscription, now)

    async def _poll_subscription(
        self, db: Session, subscription: MarketDataSubscription, now: datetime
    ) -> None:
        # The subscription list may have been read before another feed finished polling.
        db.refresh(subscription)
        if not subscription.enabled:
            return
        subscription.last_polled_at = now
        try:
            service = CandleIngestionService(db, get_secret_store())
            delta = timeframe_delta(subscription.timeframe)
            start = service.latest_close_time(
                subscription.instrument_id, subscription.timeframe
            ) or (now - delta * 2)
            await service.sync(
                subscription.workspace_id,
                subscription.instrument_id,
                subscription.timeframe,
                start,
                now,
                "complete",
            )
            subscription.last_success_at = now
            subscription.last_error_code = None
            db.commit()
        except Exception as exc:
            db.rollback()
            current = db.get(MarketDataSubscription, subscription.id)
            if current is not None:
                current.last_polled_at = now
                current.last_error_code = market_data_error_code(exc)
                db.commit()


market_data_worker = MarketDataPollingWorker()
