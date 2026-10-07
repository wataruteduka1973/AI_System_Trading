"""Reconciling an account's orders, fills, ledger and positions (FR-ORD-06, 取引停止マトリクス
「注文・台帳不整合」, docs/plans/ledger-reconciliation.md).

For paper trading the internal ledger is the source of truth (要件 §235), so there is no exchange to
compare with; what can go wrong is the internal records disagreeing with one another. One fill is
written to four places (`fill`, the order's `filled_quantity`/status, the ledger, the position) by
`order_flow.place_order` in one transaction, so they agree unless a bug, a manual edit or a partial
restore broke that. Each check below is an invariant of those writes:

- every fill has its ledger transaction, and the entries add up to the fill (cash = ∓price×quantity,
  fee = -fee_amount); no fill-type ledger entry is left without a fill;
- an order's `filled_quantity` is the sum of its fills, and a `filled` order is filled completely;
- an order's status appears in its own status history (a legacy order without history is skipped);
- there is at most one open position per instrument, and its signed quantity is the net of the
  fills (buys - sells); a closed one holds no quantity;
- the position rows' `realized_pnl` add up to the ledger's `realized_pnl` entries;
- cash from the fills + the open position's signed cost basis = the realized P&L. This ties
  `average_entry_price` to the fills without replaying the averaging a second time.

The checks are a pure function of the rows (`reconcile`), so they are tested without a database;
`reconcile_account` only loads the rows. Amounts are compared with `TOLERANCE`: the ledger rounds
each amount to 18 decimals when it is stored.

What this cannot see: a fill and its ledger both wrong in the same way (they come from one price),
deposits and `adjustment` entries (not tied to a fill), and `account_snapshot` (an observation
at a moment, which cannot be recomputed later).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trading import (
    Fill,
    LedgerEntry,
    LedgerTransaction,
    OrderStatusHistory,
    TradeOrder,
    TradingPosition,
)

TOLERANCE = Decimal("0.000000001")
FILL_ENTRY_TYPES = ("cash", "fee", "realized_pnl")
MAX_FINDINGS = 50
"""One corrupted account can produce a finding per fill; the rest add nothing."""


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    """Japanese, ready to show."""
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderRow:
    id: UUID
    instrument_id: UUID
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    status: str


@dataclass(frozen=True)
class FillRow:
    id: UUID
    order_id: UUID
    price: Decimal
    quantity: Decimal
    fee_amount: Decimal


@dataclass(frozen=True)
class EntryRow:
    fill_id: UUID | None
    entry_type: str
    amount: Decimal


@dataclass(frozen=True)
class PositionRow:
    id: UUID
    instrument_id: UUID
    side: str
    quantity: Decimal
    average_entry_price: Decimal | None
    realized_pnl: Decimal
    status: str


@dataclass(frozen=True)
class AccountRows:
    orders: list[OrderRow]
    fills: list[FillRow]
    entries: list[EntryRow]
    """Only the `FILL_ENTRY_TYPES` entries of the account."""
    fill_transaction_refs: list[UUID | None]
    """`reference_id` of each fill-type ledger transaction."""
    history: dict[UUID, set[str]]
    """`to_status` values recorded for each order that has any history."""
    positions: list[PositionRow]


def _differs(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) > TOLERANCE


def _sum(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0))


def reconcile(rows: AccountRows) -> list[Finding]:
    findings: list[Finding] = []
    orders = {order.id: order for order in rows.orders}
    fills_by_order: dict[UUID, list[FillRow]] = defaultdict(list)
    for fill in rows.fills:
        fills_by_order[fill.order_id].append(fill)
    entries_by_fill: dict[UUID, list[EntryRow]] = defaultdict(list)
    for entry in rows.entries:
        if entry.fill_id is not None:
            entries_by_fill[entry.fill_id].append(entry)
    ledger_refs = {ref for ref in rows.fill_transaction_refs if ref is not None}
    fill_ids = {fill.id for fill in rows.fills}

    for fill in rows.fills:
        order = orders[fill.order_id]
        if fill.id not in ledger_refs:
            findings.append(
                Finding(
                    "fill_without_ledger",
                    "約定に対応する台帳の取引がありません",
                    {"fill_id": str(fill.id), "order_id": str(order.id)},
                )
            )
            continue
        entries = entries_by_fill.get(fill.id, [])
        notional = fill.price * fill.quantity
        expected_cash = -notional if order.side == "buy" else notional
        cash = _sum([e.amount for e in entries if e.entry_type == "cash"])
        if _differs(cash, expected_cash):
            findings.append(
                Finding(
                    "ledger_cash_mismatch",
                    "約定の代金と台帳の現金の記録が合いません",
                    {"fill_id": str(fill.id), "expected": str(expected_cash), "ledger": str(cash)},
                )
            )
        fee = _sum([e.amount for e in entries if e.entry_type == "fee"])
        if _differs(fee, -fill.fee_amount):
            findings.append(
                Finding(
                    "ledger_fee_mismatch",
                    "約定の手数料と台帳の手数料の記録が合いません",
                    {
                        "fill_id": str(fill.id),
                        "expected": str(-fill.fee_amount),
                        "ledger": str(fee),
                    },
                )
            )

    orphan_entries = [e for e in rows.entries if e.fill_id is None]
    if orphan_entries:
        findings.append(
            Finding(
                "ledger_entry_without_fill",
                f"約定に結びつかない台帳の記録が{len(orphan_entries)}件あります",
                {"count": str(len(orphan_entries))},
            )
        )
    missing = [ref for ref in ledger_refs if ref not in fill_ids]
    if missing:
        findings.append(
            Finding(
                "ledger_transaction_without_fill",
                f"約定が存在しない台帳の取引が{len(missing)}件あります",
                {"count": str(len(missing)), "reference_id": str(missing[0])},
            )
        )

    for order in rows.orders:
        filled = _sum([f.quantity for f in fills_by_order.get(order.id, [])])
        if _differs(order.filled_quantity, filled):
            findings.append(
                Finding(
                    "order_filled_quantity_mismatch",
                    "注文の約定数量と約定の合計が合いません",
                    {
                        "order_id": str(order.id),
                        "order": str(order.filled_quantity),
                        "fills": str(filled),
                    },
                )
            )
        elif order.status == "filled" and _differs(order.filled_quantity, order.quantity):
            findings.append(
                Finding(
                    "order_filled_incompletely",
                    "全部約定の注文が、注文数量どおりに約定していません",
                    {
                        "order_id": str(order.id),
                        "quantity": str(order.quantity),
                        "filled": str(order.filled_quantity),
                    },
                )
            )
        recorded = rows.history.get(order.id)
        if recorded is not None and order.status not in recorded:
            findings.append(
                Finding(
                    "order_status_not_in_history",
                    "注文の状態が、状態の履歴に記録されていません",
                    {"order_id": str(order.id), "status": order.status},
                )
            )

    findings.extend(_reconcile_positions(rows, orders, fills_by_order, entries_by_fill))
    return findings[:MAX_FINDINGS]


def _reconcile_positions(
    rows: AccountRows,
    orders: dict[UUID, OrderRow],
    fills_by_order: dict[UUID, list[FillRow]],
    entries_by_fill: dict[UUID, list[EntryRow]],
) -> list[Finding]:
    findings: list[Finding] = []
    instruments = {order.instrument_id for order in rows.orders} | {
        position.instrument_id for position in rows.positions
    }
    for instrument_id in sorted(instruments, key=str):
        label = {"instrument_id": str(instrument_id)}
        open_positions = [
            p for p in rows.positions if p.instrument_id == instrument_id and p.status == "open"
        ]
        if len(open_positions) > 1:
            findings.append(
                Finding(
                    "multiple_open_positions",
                    "同じ銘柄に、建玉が2つ以上開いています",
                    {**label, "count": str(len(open_positions))},
                )
            )
            continue
        position = open_positions[0] if open_positions else None
        for closed in rows.positions:
            if (
                closed.instrument_id == instrument_id
                and closed.status != "open"
                and closed.quantity
            ):
                findings.append(
                    Finding(
                        "closed_position_with_quantity",
                        "閉じた建玉に数量が残っています",
                        {**label, "position_id": str(closed.id), "quantity": str(closed.quantity)},
                    )
                )

        net = Decimal(0)
        cash = Decimal(0)
        realized_ledger = Decimal(0)
        for order in rows.orders:
            if order.instrument_id != instrument_id:
                continue
            for fill in fills_by_order.get(order.id, []):
                net += fill.quantity if order.side == "buy" else -fill.quantity
                notional = fill.price * fill.quantity
                cash += -notional if order.side == "buy" else notional
                for entry in entries_by_fill.get(fill.id, []):
                    if entry.entry_type == "realized_pnl":
                        realized_ledger += entry.amount

        signed_quantity = Decimal(0)
        signed_cost = Decimal(0)
        if position is not None:
            direction = Decimal(1) if position.side == "long" else Decimal(-1)
            signed_quantity = direction * position.quantity
            if position.quantity <= 0:
                findings.append(
                    Finding(
                        "open_position_without_quantity",
                        "開いている建玉の数量が0以下です",
                        {**label, "position_id": str(position.id)},
                    )
                )
            if position.average_entry_price is None:
                findings.append(
                    Finding(
                        "open_position_without_entry_price",
                        "開いている建玉に平均建値がありません",
                        {**label, "position_id": str(position.id)},
                    )
                )
            else:
                signed_cost = direction * position.average_entry_price * position.quantity
        if _differs(signed_quantity, net):
            findings.append(
                Finding(
                    "position_quantity_mismatch",
                    "建玉の数量が、約定の売買の差し引きと合いません",
                    {**label, "position": str(signed_quantity), "fills": str(net)},
                )
            )
        realized_positions = _sum(
            [p.realized_pnl for p in rows.positions if p.instrument_id == instrument_id]
        )
        if _differs(realized_positions, realized_ledger):
            findings.append(
                Finding(
                    "realized_pnl_mismatch",
                    "建玉の実現損益が、台帳の実現損益と合いません",
                    {**label, "positions": str(realized_positions), "ledger": str(realized_ledger)},
                )
            )
        if not _differs(signed_quantity, net) and _differs(cash + signed_cost, realized_ledger):
            findings.append(
                Finding(
                    "position_cost_basis_mismatch",
                    "建玉の平均建値が、約定の代金と実現損益から見て合いません",
                    {
                        **label,
                        "cash_plus_cost_basis": str(cash + signed_cost),
                        "realized_pnl": str(realized_ledger),
                    },
                )
            )
    return findings


def load_account_rows(db: Session, account_id: UUID) -> AccountRows:
    orders = [
        OrderRow(*row)
        for row in db.execute(
            select(
                TradeOrder.id,
                TradeOrder.instrument_id,
                TradeOrder.side,
                TradeOrder.quantity,
                TradeOrder.filled_quantity,
                TradeOrder.status,
            ).where(TradeOrder.account_id == account_id)
        ).all()
    ]
    fills = [
        FillRow(*row)
        for row in db.execute(
            select(Fill.id, Fill.order_id, Fill.price, Fill.quantity, Fill.fee_amount)
            .join(TradeOrder, TradeOrder.id == Fill.order_id)
            .where(TradeOrder.account_id == account_id)
        ).all()
    ]
    entries = [
        EntryRow(*row)
        for row in db.execute(
            select(LedgerEntry.fill_id, LedgerEntry.entry_type, LedgerEntry.amount).where(
                LedgerEntry.account_id == account_id,
                LedgerEntry.entry_type.in_(FILL_ENTRY_TYPES),
            )
        ).all()
    ]
    refs = list(
        db.scalars(
            select(LedgerTransaction.reference_id).where(
                LedgerTransaction.account_id == account_id,
                LedgerTransaction.reference_type == "fill",
            )
        ).all()
    )
    history: dict[UUID, set[str]] = defaultdict(set)
    for order_id, to_status in db.execute(
        select(OrderStatusHistory.order_id, OrderStatusHistory.to_status)
        .join(TradeOrder, TradeOrder.id == OrderStatusHistory.order_id)
        .where(TradeOrder.account_id == account_id)
    ).all():
        history[order_id].add(to_status)
    positions = [
        PositionRow(*row)
        for row in db.execute(
            select(
                TradingPosition.id,
                TradingPosition.instrument_id,
                TradingPosition.side,
                TradingPosition.quantity,
                TradingPosition.average_entry_price,
                TradingPosition.realized_pnl,
                TradingPosition.status,
            ).where(TradingPosition.account_id == account_id)
        ).all()
    ]
    return AccountRows(
        orders=orders,
        fills=fills,
        entries=entries,
        fill_transaction_refs=refs,
        history=dict(history),
        positions=positions,
    )


def reconcile_account(db: Session, account_id: UUID) -> list[Finding]:
    return reconcile(load_account_rows(db, account_id))
