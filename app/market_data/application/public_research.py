"""Provisioning and backfill for the "binance_public" research-only
`Instrument` (2026-09-26, per user decision -- see
`app/exchanges/binance_public.py`'s module docstring for why this exists:
Binance Spot Testnet's own BTCJPY history is too illiquid to validate a
strategy against, so real production klines are fetched read-only instead,
under their own Instrument so they never mix with the Testnet price series
actually used for paper execution).

Kept separate from `use_cases.py`: every function there assumes a credentialed
`ExchangeConnection`/`WorkspaceAccountSelection` exists for the instrument in
question, which a public-data research instrument deliberately has none of.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.exchanges.binance_public import BinancePublicClient
from app.exchanges.types import timeframe_delta
from app.market_data.infrastructure.candle_store import persist_internal_gaps, upsert_candle_points
from app.models.audit import AuditLog
from app.models.connections import Exchange, Market
from app.models.instruments import Instrument
from app.models.market_data import Candle, MarketDataGap
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)

RESEARCH_EXCHANGE_CODE = "binance_public"
_PAGE_SIZE_CANDLES = 1000  # matches Binance's own per-request cap (MAX_KLINES_PER_REQUEST)


_GAP_REASON = "internal_missing_candles"
_AUDIT_WINDOW_SAMPLES = 20


class PublicResearchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PublicFetchCounts(NamedTuple):
    inserted: int
    updated: int
    rejected: int


def is_research_instrument(db: Session, instrument_id: UUID) -> bool:
    """Whether `instrument_id` belongs to the public, credential-less research
    exchange. Such an instrument has no connection or account selection by design,
    so only read paths may admit it on that basis -- never collection, backfill
    requests, subscriptions, streams or orders."""
    exchange_code = db.scalar(
        select(Exchange.code)
        .join(Instrument, Instrument.exchange_id == Exchange.id)
        .where(Instrument.id == instrument_id)
    )
    return exchange_code == RESEARCH_EXCHANGE_CODE


def resolve_audit_workspace_id(db: Session, workspace_id: UUID | None) -> UUID:
    """`audit_log` rows belong to a workspace, but public research data does not. The
    operator names the workspace the run is recorded under; with none named, the only
    workspace is used, and more than one is an error rather than a guess."""
    if workspace_id is not None:
        if db.get(Workspace, workspace_id) is None:
            raise PublicResearchError("workspace_not_found", "Workspace not found")
        return workspace_id
    ids = list(db.scalars(select(Workspace.id).limit(2)).all())
    if len(ids) != 1:
        raise PublicResearchError(
            "workspace_required",
            "Pass --workspace-id: the audit record needs a workspace "
            + ("(none exist)" if not ids else "(several exist)"),
        )
    return ids[0]


def _record_audit(
    db: Session,
    workspace_id: UUID,
    action: str,
    instrument: Instrument,
    details: dict[str, object],
) -> None:
    """Public data carries no credentials; `details` holds only symbol, range, counts
    and source names."""
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=None,
            action=action,
            resource_type="instrument",
            resource_id=instrument.id,
            before_data=None,
            after_data={"symbol": instrument.symbol, "source": RESEARCH_EXCHANGE_CODE, **details},
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )


def find_public_research_instrument(db: Session, symbol: str) -> Instrument | None:
    """The already-fetched research instrument for `symbol`, or None if
    `scripts/fetch_binance_public_history.py` has not been run for it yet."""
    return db.scalar(
        select(Instrument)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
        .where(Exchange.code == RESEARCH_EXCHANGE_CODE, Instrument.symbol == symbol)
    )


async def ensure_public_research_instrument(
    db: Session, client: BinancePublicClient, symbol: str = "BTCJPY"
) -> Instrument:
    """Idempotent: creates the instrument on first call, refreshes its trading
    rules (tick/step size, notional floor) from Binance's real `exchangeInfo`
    every call after that. Mirrors `app/api/routes/instruments.py`'s
    `_upsert_instrument`, minus the connection/credentials it has no need for."""
    exchange = db.scalar(select(Exchange).where(Exchange.code == RESEARCH_EXCHANGE_CODE))
    if exchange is None:
        raise PublicResearchError(
            "exchange_not_seeded",
            "The binance_public exchange catalog entry is missing "
            "(run migrations -- see alembic/versions/20260926_0009_*)",
        )
    market = db.scalar(select(Market).where(Market.code == "crypto_spot"))
    if market is None:
        raise PublicResearchError(
            "market_not_seeded", "The crypto_spot market catalog entry is missing"
        )

    rules = await client.get_instrument_rules(symbol)
    instrument = db.scalar(
        select(Instrument).where(
            Instrument.exchange_id == exchange.id,
            Instrument.market_id == market.id,
            Instrument.symbol == symbol,
        )
    )
    if instrument is None:
        instrument = Instrument(exchange_id=exchange.id, market_id=market.id, symbol=symbol)
        db.add(instrument)
    instrument.base_asset = rules.base_asset
    instrument.quote_asset = rules.quote_asset
    instrument.price_scale = rules.price_scale
    instrument.quantity_scale = rules.quantity_scale
    instrument.tick_size = rules.tick_size
    instrument.step_size = rules.step_size
    instrument.min_quantity = rules.min_quantity
    instrument.max_quantity = rules.max_quantity
    instrument.min_notional = rules.min_notional
    instrument.allowed_order_types = list(rules.allowed_order_types)
    instrument.capabilities = {"research_only": True, "source": RESEARCH_EXCHANGE_CODE}
    instrument.status = "active"
    instrument.rules_synced_at = datetime.now(UTC)
    instrument.updated_at = datetime.now(UTC)
    db.flush()
    return instrument


async def _fetch_and_upsert(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    *,
    start: datetime,
    end: datetime,
    quality_status: str,
    audit_workspace_id: UUID | None = None,
    audit_summary: bool = False,
) -> PublicFetchCounts:
    """Fetch `[start, end)` in `_PAGE_SIZE_CANDLES`-sized pages and upsert + commit
    each page's final candles as it arrives. Returns the inserted/updated/rejected
    counts (`rejected`: stored by a different source, left unchanged).

    With `audit_workspace_id`, a source conflict is always recorded
    (`public_candles.source_conflict`), and `audit_summary` also records the run
    (`public_candles.backfill`). Both are written once the pages are stored, so a run
    that dies midway leaves its candles but no summary."""
    delta = timeframe_delta(timeframe)
    cursor = start
    total_inserted = total_updated = total_rejected = 0
    while cursor < end:
        page_end = min(end, cursor + delta * _PAGE_SIZE_CANDLES)
        points = await client.get_candles(instrument.symbol, timeframe, cursor, page_end)
        final_points = [point for point in points if point.is_final and point.close_time <= end]
        if final_points:
            inserted, updated, rejected = upsert_candle_points(
                db, instrument.id, timeframe, RESEARCH_EXCHANGE_CODE, quality_status, final_points
            )
            total_inserted += inserted
            total_updated += updated
            total_rejected += rejected
            db.commit()
        cursor = page_end
    if total_rejected:
        logger.warning(
            "public candles %s %s: %d candles already stored by another source were left unchanged",
            instrument.symbol,
            timeframe,
            total_rejected,
        )
    if audit_workspace_id is not None and (audit_summary or total_rejected):
        details: dict[str, object] = {
            "timeframe": timeframe,
            "from_time": start.isoformat(),
            "to_time": end.isoformat(),
            "quality_status": quality_status,
            "inserted": total_inserted,
            "updated": total_updated,
            "rejected": total_rejected,
        }
        if audit_summary:
            _record_audit(db, audit_workspace_id, "public_candles.backfill", instrument, details)
        if total_rejected:
            _record_audit(
                db, audit_workspace_id, "public_candles.source_conflict", instrument, details
            )
        db.commit()
    return PublicFetchCounts(total_inserted, total_updated, total_rejected)


async def backfill_public_klines(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    days: int,
    *,
    audit_workspace_id: UUID | None = None,
) -> PublicFetchCounts:
    """Fetches `[now - days, now)` page by page, committing each page rather than
    holding one huge uncommitted transaction for a full year of 1m data (~526
    pages). Caller is responsible for pacing between calls (see
    `scripts/fetch_binance_public_history.py`) -- this function makes no attempt at
    its own rate limiting. With `audit_workspace_id` the run is recorded in
    `audit_log` (see `_fetch_and_upsert`)."""
    end = datetime.now(UTC)
    return await _fetch_and_upsert(
        db,
        client,
        instrument,
        timeframe,
        start=end - timedelta(days=days),
        end=end,
        quality_status="backfilled",
        audit_workspace_id=audit_workspace_id,
        audit_summary=True,
    )


async def refresh_public_klines(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    *,
    initial_bars: int,
    now: datetime,
) -> int:
    """Keep a paper-trading bot's public price series current
    (docs/plans/paper-trading-live-data.md Unit 1). Returns the number of new
    candles stored.

    Calls Binance only once the bar after the latest stored one should have
    closed -- the trading worker polls every few seconds, and a 4h series needs
    one request per bar, not one per poll. Fetching restarts at the latest
    stored bar, so a gap left while the worker was stopped is filled on the
    next refresh. With no stored bars it fetches the last `initial_bars` bars,
    enough history for the strategy's lookback. Stored as `complete`: these
    are ordinary closed bars, not a historical backfill."""
    delta = timeframe_delta(timeframe)
    latest = db.scalar(
        select(Candle)
        .where(
            Candle.instrument_id == instrument.id,
            Candle.timeframe == timeframe,
            Candle.is_final.is_(True),
        )
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if latest is not None and latest.close_time + delta > now:
        return 0
    start = latest.open_time if latest is not None else now - delta * initial_bars
    counts = await _fetch_and_upsert(
        db,
        client,
        instrument,
        timeframe,
        start=start,
        end=now,
        quality_status="complete",
    )
    return counts.inserted


@dataclass(frozen=True)
class PublicValidation:
    """Outcome of `validate_public_series` for one instrument and time frame."""

    timeframe: str
    stored_count: int
    missing_before: int
    missing_after: int
    unfillable_windows: list[dict[str, object]]


async def validate_public_series(
    db: Session,
    client: BinancePublicClient,
    instrument: Instrument,
    timeframe: str,
    *,
    audit_workspace_id: UUID,
) -> PublicValidation:
    """Phase A's gap validation for a public series (docs/plans/candle-chart-and-coverage.md
    Phase C): detect internal gaps, ask Binance again for each, and re-check. A gap that
    is still missing afterwards is one the source itself does not have (Binance's own
    maintenance windows) -- it is kept as an `ignored` `market_data_gap` row so the
    next run does not ask again, and listed in the audit record.

    24/7 market: every slot between the first and last stored candle is expected."""
    delta = timeframe_delta(timeframe)
    stored_count, first_open, last_open = db.execute(
        select(func.count(Candle.id), func.min(Candle.open_time), func.max(Candle.open_time)).where(
            Candle.instrument_id == instrument.id,
            Candle.timeframe == timeframe,
            Candle.is_final.is_(True),
        )
    ).one()
    if not stored_count or first_open is None or last_open is None:
        return PublicValidation(timeframe, 0, 0, 0, [])
    range_to = last_open + delta
    before = persist_internal_gaps(
        db,
        instrument_id=instrument.id,
        timeframe=timeframe,
        requested_from=first_open,
        requested_to=range_to,
    )
    db.commit()
    open_windows = list(db.scalars(_gap_rows(instrument, timeframe, "open")).all())
    for window in open_windows:
        await _fetch_and_upsert(
            db,
            client,
            instrument,
            timeframe,
            start=window.from_time,
            end=window.to_time,
            quality_status="backfilled",
        )
    after = persist_internal_gaps(
        db,
        instrument_id=instrument.id,
        timeframe=timeframe,
        requested_from=first_open,
        requested_to=range_to,
    )
    for window in db.scalars(_gap_rows(instrument, timeframe, "open")).all():
        window.status = "ignored"
    unfillable = [gap.as_dict() for gap in after]
    result = PublicValidation(
        timeframe,
        stored_count,
        sum(gap.missing_count for gap in before),
        sum(gap.missing_count for gap in after),
        unfillable,
    )
    _record_audit(
        db,
        audit_workspace_id,
        "public_candles.gap_validation",
        instrument,
        {
            "timeframe": timeframe,
            "from_time": first_open.isoformat(),
            "to_time": range_to.isoformat(),
            "stored_count": stored_count,
            "missing_before": result.missing_before,
            "missing_after": result.missing_after,
            "unfillable_window_count": len(unfillable),
            "unfillable_windows": unfillable[:_AUDIT_WINDOW_SAMPLES],
        },
    )
    db.commit()
    return result


def _gap_rows(instrument: Instrument, timeframe: str, status: str) -> Select[Any]:
    return select(MarketDataGap).where(
        MarketDataGap.instrument_id == instrument.id,
        MarketDataGap.timeframe == timeframe,
        MarketDataGap.reason_code == _GAP_REASON,
        MarketDataGap.status == status,
    )
