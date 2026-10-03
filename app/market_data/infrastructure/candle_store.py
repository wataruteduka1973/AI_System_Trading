"""Reads and writes stored candles and their gaps: the upsert against
`uq_candle_business_key`, coverage of a stored range, and the open/resolved
`market_data_gap` rows (docs/plans/market-data-services-consolidation.md)."""

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
from app.market_data.infrastructure.backfill_locks import advisory_lock_key
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import Candle, MarketDataGap


def upsert_candle_points(
    db: Session,
    instrument_id: UUID,
    timeframe: str,
    source: str,
    quality_status: str,
    points: list[CandlePoint],
) -> tuple[int, int]:
    """Extracted from the former `CandleIngestionService._upsert_points` (2026-09-26, when
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
                advisory_lock_key("market-data-gap", instrument_id, timeframe)
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
