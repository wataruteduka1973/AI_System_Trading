import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Boolean, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.exchanges.types import CandlePoint, timeframe_delta
from app.market_data.domain.coverage import (
    GapWindow,
    classify_candle_coverage,
    find_internal_gaps,
    is_expected_market_time,
)
from app.models.connections import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    WorkspaceAccountSelection,
)
from app.models.instruments import Instrument
from app.models.market_data import BackfillJob, Candle, MarketDataGap
from app.services.secrets import LocalEncryptedSecretStore


class MarketDataAccessError(RuntimeError):
    def __init__(self, message: str, code: str = "configuration_error") -> None:
        super().__init__(message)
        self.code = code


class DuplicateBackfillError(RuntimeError):
    pass


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
        matching_gap = existing_by_key.get(key)
        if matching_gap is not None:
            matching_gap.expected_count = gap.expected_count
            matching_gap.missing_count = gap.missing_count
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
        if is_expected_market_time(exchange_code, candidate):
            expected += 1
            if candidate not in stored_open_times:
                return False
        candidate += delta
    return expected > 0


def _advisory_lock_key(namespace: str, *parts: object) -> int:
    lock_material = ":".join((namespace, *(str(part) for part in parts))).encode()
    return int.from_bytes(hashlib.blake2b(lock_material, digest_size=8).digest(), signed=True)


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


def upsert_candle_points(
    db: Session,
    instrument_id: UUID,
    timeframe: str,
    source: str,
    quality_status: str,
    points: list[CandlePoint],
) -> tuple[int, int]:
    """Extracted from `CandleIngestionService._upsert_points` (2026-09-26, when
    `app/market_data/application/public_research.py` needed the identical
    upsert against `uq_candle_business_key` without going through the rest of
    that service's credentialed-account machinery). Returns (inserted,
    updated)."""
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
    returning_statement = statement.on_conflict_do_update(
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
    ).returning(literal_column("xmax = 0", Boolean))
    inserted_flags = list(db.scalars(returning_statement).all())
    inserted = sum(bool(flag) for flag in inserted_flags)
    return inserted, len(inserted_flags) - inserted


class CandleIngestionService:
    def __init__(
        self,
        db: Session,
        secret_store: LocalEncryptedSecretStore,
    ) -> None:
        self.db = db
        self.secret_store = secret_store

    def validate_configuration(self, workspace_id: UUID, instrument_id: UUID) -> None:
        """Check local access and decryptability without sending any exchange request."""
        _instrument, exchange, connection = self._resolve_access(workspace_id, instrument_id)
        credentials = self._load_credentials(connection)
        required = ("token",) if exchange.code == "oanda" else ("api_key", "secret_key")
        if not all(credentials.get(key) for key in required):
            raise MarketDataAccessError(
                "Exchange credentials are incomplete", "credentials_missing"
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
        instrument, exchange, connection = row
        return instrument, exchange, connection

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

    def _upsert_points(
        self,
        instrument_id: UUID,
        timeframe: str,
        source: str,
        quality_status: str,
        points: list[CandlePoint],
    ) -> tuple[int, int]:
        return upsert_candle_points(
            self.db, instrument_id, timeframe, source, quality_status, points
        )
