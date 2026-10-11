"""The missing-candle report (FR-UI-10, docs/plans/data-gaps.md): which stored series have holes,
how many candles are missing, what is being done about it, and which bots trade the instrument.

A `market_data_gap` is a stretch of candles that should exist between two stored ones. Gaps are
found when a backfill finishes or when a public history is imported
(`candle_store.persist_internal_gaps`), and stay `open` until a later check finds the stretch
filled (`resolved`) -- or, for a public history whose source itself lacks the candles (the
exchange's maintenance windows), are kept as `ignored` so the next import does not ask again.

A workspace sees the gaps of the instruments it can use: those of an exchange it has selected an
account on, and the public research instruments. Only the first can be filled from here: filling
queues a backfill job for the stretch, which the market-data worker runs
(`use_cases.enqueue_gap_backfill`); a public history is re-imported by its own script.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from app.market_data.infrastructure.access import readable_instrument_ids
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import BackfillJob, MarketDataGap
from app.models.strategy import TradingBot

RESEARCH_EXCHANGE = "binance_public"
ACTIVE_JOB_STATUSES = ("queued", "running")
ACTIVE_BOT_STATES = ("running", "paused")
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class GapRow:
    gap: MarketDataGap
    symbol: str
    exchange_code: str
    latest_job: BackfillJob | None
    affected_bots: list[str] = field(default_factory=list)

    @property
    def can_backfill(self) -> bool:
        """An open gap of an instrument this workspace collects, with nothing already running."""
        return (
            self.gap.status == "open"
            and self.exchange_code != RESEARCH_EXCHANGE
            and (self.latest_job is None or self.latest_job.status not in ACTIVE_JOB_STATUSES)
        )


@dataclass(frozen=True)
class GapSummaryRow:
    instrument_id: UUID
    symbol: str
    exchange_code: str
    timeframe: str
    open_count: int
    open_missing: int
    ignored_count: int
    resolved_count: int


def _visible(statement: Select, workspace_id: UUID) -> Select:
    """Restricts a select over `market_data_gap` joined to `Instrument` and `Exchange` to the
    instruments this workspace can read: the public research ones, and those of an exchange it
    has a selected, verified account on (the same conditions as every other market-data read)."""
    return statement.where(
        or_(
            Exchange.code == RESEARCH_EXCHANGE,
            MarketDataGap.instrument_id.in_(readable_instrument_ids(workspace_id)),
        )
    )


def list_gaps(
    db: Session,
    workspace_id: UUID,
    *,
    statuses: list[str],
    instrument_id: UUID | None,
    timeframe: str | None,
    limit: int,
    offset: int = 0,
) -> tuple[int, list[GapRow]]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    base = (
        select(MarketDataGap, Instrument.symbol, Exchange.code)
        .join(Instrument, Instrument.id == MarketDataGap.instrument_id)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
    )
    base = _visible(base, workspace_id)
    if statuses:
        base = base.where(MarketDataGap.status.in_(statuses))
    if instrument_id is not None:
        base = base.where(MarketDataGap.instrument_id == instrument_id)
    if timeframe is not None:
        base = base.where(MarketDataGap.timeframe == timeframe)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(MarketDataGap.from_time.desc(), MarketDataGap.id).limit(limit).offset(offset)
    ).all()
    gaps = [row[0] for row in rows]
    jobs = _latest_jobs(db, workspace_id, [gap.id for gap in gaps])
    bots = _affected_bots(db, workspace_id, {gap.instrument_id for gap in gaps})
    return total, [
        GapRow(
            gap=gap,
            symbol=symbol,
            exchange_code=exchange_code,
            latest_job=jobs.get(gap.id),
            affected_bots=bots.get(gap.instrument_id, []),
        )
        for gap, symbol, exchange_code in rows
    ]


def _latest_jobs(db: Session, workspace_id: UUID, gap_ids: list[UUID]) -> dict[UUID, BackfillJob]:
    if not gap_ids:
        return {}
    latest: dict[UUID, BackfillJob] = {}
    for job in db.scalars(
        select(BackfillJob)
        .where(BackfillJob.workspace_id == workspace_id, BackfillJob.gap_id.in_(gap_ids))
        .order_by(BackfillJob.created_at)
    ).all():
        if job.gap_id is not None:
            latest[job.gap_id] = job  # ascending, so the last one written is the newest
    return latest


def _affected_bots(
    db: Session, workspace_id: UUID, instrument_ids: set[UUID]
) -> dict[UUID, list[str]]:
    """The bots that are running or paused on each instrument -- the ones that trade on its
    candles. (A gap is per timeframe; a bot decides on its own, so this names the instrument's
    bots without claiming which bar the hole falls in.)"""
    if not instrument_ids:
        return {}
    result: dict[UUID, list[str]] = {}
    for instrument_id, name in db.execute(
        select(TradingBot.instrument_id, TradingBot.name)
        .where(
            TradingBot.workspace_id == workspace_id,
            TradingBot.instrument_id.in_(instrument_ids),
            TradingBot.actual_state.in_(ACTIVE_BOT_STATES),
        )
        .order_by(TradingBot.name)
    ).all():
        result.setdefault(instrument_id, []).append(name)
    return result


def summarize_gaps(db: Session, workspace_id: UUID) -> list[GapSummaryRow]:
    """Per instrument and timeframe: how many gaps are open (and how many candles they miss),
    ignored and resolved. Series with open gaps come first."""
    open_ = MarketDataGap.status == "open"
    statement = (
        select(
            MarketDataGap.instrument_id,
            Instrument.symbol,
            Exchange.code,
            MarketDataGap.timeframe,
            func.count().filter(open_),
            func.coalesce(func.sum(case((open_, MarketDataGap.missing_count), else_=0)), 0),
            func.count().filter(MarketDataGap.status == "ignored"),
            func.count().filter(MarketDataGap.status == "resolved"),
        )
        .join(Instrument, Instrument.id == MarketDataGap.instrument_id)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
        .group_by(
            MarketDataGap.instrument_id, Instrument.symbol, Exchange.code, MarketDataGap.timeframe
        )
    )
    rows = db.execute(_visible(statement, workspace_id)).all()
    summaries = [
        GapSummaryRow(
            instrument_id=row[0],
            symbol=row[1],
            exchange_code=row[2],
            timeframe=row[3],
            open_count=row[4],
            open_missing=int(row[5]),
            ignored_count=row[6],
            resolved_count=row[7],
        )
        for row in rows
    ]
    return sorted(summaries, key=lambda s: (-s.open_missing, s.symbol, s.timeframe))
