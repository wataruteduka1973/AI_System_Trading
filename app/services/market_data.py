import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.exchanges.binance import (
    BinanceApiError,
    BinanceAuthenticationError,
    BinanceSpotTestnetClient,
)
from app.exchanges.oanda import OandaApiError, OandaAuthenticationError, OandaPracticeClient
from app.exchanges.types import CandlePoint, timeframe_delta
from app.models.catalog import (
    BackfillJob,
    Candle,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    MarketDataSubscription,
    WorkspaceAccountSelection,
)
from app.services.secrets import LocalEncryptedSecretStore, get_secret_store


class MarketDataAccessError(RuntimeError):
    pass


def classify_candle_coverage(
    *,
    exchange_code: str | None,
    timeframe: str,
    requested_from: datetime | None,
    requested_to: datetime | None,
    stored_count: int,
    actual_from: datetime | None,
    actual_to: datetime | None,
) -> dict[str, object]:
    delta = timeframe_delta(timeframe)
    expected_count: int | None = None
    missing_count: int | None = None
    coverage_status = "empty"
    if stored_count and actual_from is not None and actual_to is not None:
        if exchange_code == "binance":
            if requested_from is not None and requested_to is not None:
                expected_count = max(0, int((requested_to - requested_from) / delta))
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
    return classify_candle_coverage(
        exchange_code=exchange_code,
        timeframe=timeframe,
        requested_from=requested_from,
        requested_to=requested_to,
        stored_count=stored_count,
        actual_from=actual_from,
        actual_to=actual_to,
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

    async def sync(
        self,
        workspace_id: UUID,
        instrument_id: UUID,
        timeframe: str,
        start: datetime,
        end: datetime,
        quality_status: str,
    ) -> int:
        instrument, exchange, connection = self._resolve_access(workspace_id, instrument_id)
        credentials = self._load_credentials(connection)
        cursor = start.astimezone(UTC)
        end = end.astimezone(UTC)
        page_size = 4900 if exchange.code == "oanda" else 950
        delta = timeframe_delta(timeframe)
        rows_written = 0
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
            final_points = [point for point in points if point.is_final and point.close_time <= end]
            if final_points:
                self._upsert_points(
                    instrument.id, timeframe, exchange.code, quality_status, final_points
                )
                rows_written += len(final_points)
            cursor = page_end
        return rows_written

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
            raise MarketDataAccessError("Selected connection credentials are missing")
        try:
            return self.secret_store.get(connection.secret_ref)
        except (KeyError, ValueError) as exc:
            raise MarketDataAccessError("Selected connection credentials cannot be loaded") from exc

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
    ) -> None:
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
        )
        self.db.execute(statement)


def market_data_error_code(exc: Exception) -> str:
    if isinstance(exc, (OandaAuthenticationError, BinanceAuthenticationError)):
        return "authentication_failed"
    if isinstance(exc, (OandaApiError, BinanceApiError)):
        return "communication_failed"
    if isinstance(exc, MarketDataAccessError):
        return "configuration_error"
    if isinstance(exc, HTTPException):
        return "configuration_error"
    return "internal_error"


async def run_backfill_job(job_id: UUID) -> None:
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
            job.rows_written = await service.sync(
                job.workspace_id,
                job.instrument_id,
                job.timeframe,
                job.from_time,
                job.to_time,
                "backfilled",
            )
            job.status = "succeeded"
            job.validation_result = {
                "final_candles_only": True,
                "duplicates": "upserted",
                **build_candle_coverage(
                    db,
                    job.instrument_id,
                    job.timeframe,
                    job.from_time,
                    job.to_time,
                ),
            }
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
                    select(MarketDataSubscription).where(
                        MarketDataSubscription.enabled.is_(True)
                    )
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
