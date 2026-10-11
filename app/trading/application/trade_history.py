"""The trade history for the workspace's paper accounts (FR-UI-12, docs/plans/trade-history.md):
every fill, with the order behind it, the bot that traded, what it made when it closed something,
and why the order was placed; and the positions, open and closed.

Everything is read off what the order flow already records (`fill`, `trade_order`, `ledger_entry`,
`trading_position`) -- nothing is stored for this. A fill is one execution; this paper system fills
each order whole and at once, so a fill and its order are one-to-one.

The bot of a fill is the bot trading that instrument on that account (one account backs one bot).
The reason an order was placed is read from its `client_order_id`, which the order's creator names
(`bot_evaluation`'s `stop-`, `dote-close-` and `dummy-` ids, `emergency_stop`'s `emergency-close-`);
an id with no known prefix is a manual order.

Fills page by a keyset (`executed_at`, `id`), newest first, so a page does not shift when a new fill
arrives. Totals are per quote asset: JPY and USDT are not added together.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.orm import Session

from app.models.instruments import Instrument
from app.models.strategy import TradingBot
from app.models.trading import Fill, LedgerEntry, TradeOrder, TradingAccount, TradingPosition

MAX_PAGE_SIZE = 200

REASON_BY_PREFIX = (
    ("stop-", "stop_loss"),
    ("dote-close-", "reversal_close"),
    ("emergency-close-", "emergency_close"),
    ("dummy-", "signal"),
)


def order_reason(client_order_id: str) -> str:
    """Why an order was placed: the strategy's signal, its stop-loss, closing before a reversal,
    an emergency stop, or (no known prefix) by hand."""
    for prefix, reason in REASON_BY_PREFIX:
        if client_order_id.startswith(prefix):
            return reason
    return "manual"


@dataclass(frozen=True)
class FillFilters:
    account_id: UUID | None = None
    bot_id: UUID | None = None
    instrument_id: UUID | None = None
    side: str | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None


@dataclass(frozen=True)
class FillRow:
    fill: Fill
    order: TradeOrder
    symbol: str
    quote_asset: str
    bot_id: UUID | None
    bot_name: str | None
    realized_pnl: Decimal
    """What closing (part of) a position made on this fill, before fees; 0 for an opening one."""
    reason: str


@dataclass(frozen=True)
class FillTotals:
    quote_asset: str
    fills: int
    fees: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class FillPage:
    items: list[FillRow]
    next_before: tuple[datetime, UUID] | None
    totals: list[FillTotals] = field(default_factory=list)


@dataclass(frozen=True)
class PositionRow:
    position: TradingPosition
    symbol: str
    quote_asset: str
    bot_id: UUID | None
    bot_name: str | None


def _workspace_bots(db: Session, workspace_id: UUID) -> dict[tuple[UUID, UUID], TradingBot]:
    """The bot of each (account, instrument) pair; the oldest when two share one."""
    bots: dict[tuple[UUID, UUID], TradingBot] = {}
    for bot in db.scalars(
        select(TradingBot)
        .where(TradingBot.workspace_id == workspace_id)
        .order_by(TradingBot.created_at.desc())
    ).all():
        bots[(bot.account_id, bot.instrument_id)] = bot  # descending, so the oldest ends up last
    return bots


def _fill_conditions(workspace_id: UUID, filters: FillFilters, bots: dict) -> list:
    conditions = [TradeOrder.workspace_id == workspace_id]
    if filters.bot_id is not None:
        pair = next(
            ((a, i) for (a, i), bot in bots.items() if bot.id == filters.bot_id),
            None,
        )
        if pair is None:
            conditions.append(literal(False))
        else:
            conditions.extend(
                [TradeOrder.account_id == pair[0], TradeOrder.instrument_id == pair[1]]
            )
    if filters.account_id is not None:
        conditions.append(TradeOrder.account_id == filters.account_id)
    if filters.instrument_id is not None:
        conditions.append(TradeOrder.instrument_id == filters.instrument_id)
    if filters.side is not None:
        conditions.append(TradeOrder.side == filters.side)
    if filters.from_time is not None:
        conditions.append(Fill.executed_at >= filters.from_time)
    if filters.to_time is not None:
        conditions.append(Fill.executed_at < filters.to_time)
    return conditions


def list_fills(
    db: Session,
    workspace_id: UUID,
    filters: FillFilters,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> FillPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    bots = _workspace_bots(db, workspace_id)
    conditions = _fill_conditions(workspace_id, filters, bots)
    statement = (
        select(Fill, TradeOrder, Instrument.symbol, Instrument.quote_asset)
        .join(TradeOrder, TradeOrder.id == Fill.order_id)
        .join(Instrument, Instrument.id == TradeOrder.instrument_id)
        .where(*conditions)
    )
    if before is not None:
        statement = statement.where(
            tuple_(Fill.executed_at, Fill.id) < tuple_(literal(before[0]), literal(before[1]))
        )
    rows = db.execute(
        statement.order_by(Fill.executed_at.desc(), Fill.id.desc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    realized = _realized_by_fill(db, [row[0].id for row in page])
    items = []
    for fill, order, symbol, quote_asset in page:
        bot = bots.get((order.account_id, order.instrument_id))
        items.append(
            FillRow(
                fill=fill,
                order=order,
                symbol=symbol,
                quote_asset=quote_asset,
                bot_id=bot.id if bot else None,
                bot_name=bot.name if bot else None,
                realized_pnl=realized.get(fill.id, Decimal(0)),
                reason=order_reason(order.client_order_id),
            )
        )
    more = len(rows) > limit
    return FillPage(
        items=items,
        next_before=(page[-1][0].executed_at, page[-1][0].id) if more and page else None,
        totals=_fill_totals(db, conditions),
    )


def _realized_by_fill(db: Session, fill_ids: list[UUID]) -> dict[UUID, Decimal]:
    if not fill_ids:
        return {}
    return {
        fill_id: Decimal(total)
        for fill_id, total in db.execute(
            select(LedgerEntry.fill_id, func.sum(LedgerEntry.amount))
            .where(LedgerEntry.fill_id.in_(fill_ids), LedgerEntry.entry_type == "realized_pnl")
            .group_by(LedgerEntry.fill_id)
        ).all()
        if fill_id is not None
    }


def _fill_totals(db: Session, conditions: list) -> list[FillTotals]:
    """Over every fill the filters match, not just the page: how many, the fees paid and what the
    closing ones made, per quote asset."""
    counts = db.execute(
        select(
            Instrument.quote_asset, func.count(Fill.id), func.coalesce(func.sum(Fill.fee_amount), 0)
        )
        .join(TradeOrder, TradeOrder.id == Fill.order_id)
        .join(Instrument, Instrument.id == TradeOrder.instrument_id)
        .where(*conditions)
        .group_by(Instrument.quote_asset)
    ).all()
    realized: dict[str, Decimal] = defaultdict(Decimal)
    for asset, total in db.execute(
        select(Instrument.quote_asset, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(Fill, Fill.id == LedgerEntry.fill_id)
        .join(TradeOrder, TradeOrder.id == Fill.order_id)
        .join(Instrument, Instrument.id == TradeOrder.instrument_id)
        .where(*conditions, LedgerEntry.entry_type == "realized_pnl")
        .group_by(Instrument.quote_asset)
    ).all():
        realized[asset] = Decimal(total)
    return sorted(
        (
            FillTotals(asset, count, Decimal(fees), realized.get(asset, Decimal(0)))
            for asset, count, fees in counts
        ),
        key=lambda total: total.quote_asset,
    )


def list_positions(
    db: Session,
    workspace_id: UUID,
    *,
    statuses: list[str],
    bot_id: UUID | None,
    instrument_id: UUID | None,
    limit: int,
    offset: int = 0,
) -> tuple[int, list[PositionRow]]:
    """Open positions and closed ones. A closed position keeps its side, average entry price,
    open and close times and total realized profit; its quantity is 0 (the fills hold the sizes)."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    bots = _workspace_bots(db, workspace_id)
    base = (
        select(TradingPosition, Instrument.symbol, Instrument.quote_asset)
        .join(TradingAccount, TradingAccount.id == TradingPosition.account_id)
        .join(Instrument, Instrument.id == TradingPosition.instrument_id)
        .where(TradingAccount.workspace_id == workspace_id)
    )
    if statuses:
        base = base.where(TradingPosition.status.in_(statuses))
    if instrument_id is not None:
        base = base.where(TradingPosition.instrument_id == instrument_id)
    if bot_id is not None:
        pair = next(((a, i) for (a, i), bot in bots.items() if bot.id == bot_id), None)
        if pair is None:
            base = base.where(literal(False))
        else:
            base = base.where(
                TradingPosition.account_id == pair[0], TradingPosition.instrument_id == pair[1]
            )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(
            (TradingPosition.status == "open").desc(),
            func.coalesce(TradingPosition.closed_at, TradingPosition.opened_at).desc(),
            TradingPosition.id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    items = []
    for position, symbol, quote_asset in rows:
        bot = bots.get((position.account_id, position.instrument_id))
        items.append(
            PositionRow(
                position=position,
                symbol=symbol,
                quote_asset=quote_asset,
                bot_id=bot.id if bot else None,
                bot_name=bot.name if bot else None,
            )
        )
    return total, items
