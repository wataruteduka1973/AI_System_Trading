"""The ledger reconciliation's checks on plain rows
(app/trading/application/ledger_reconciliation.py, docs/plans/ledger-reconciliation.md). The same
checks against the real writer and PostgreSQL are in test_ledger_reconciliation_postgres.py."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from app.trading.application import ledger_check
from app.trading.application import ledger_reconciliation as lr

INSTRUMENT = uuid4()


def _trade(side, quantity, price, *, fee="0", filled=None, status="filled"):
    """One order, its fill and the ledger entries `order_flow` writes for it."""
    order_id, fill_id = uuid4(), uuid4()
    quantity, price, fee = Decimal(quantity), Decimal(price), Decimal(fee)
    order = lr.OrderRow(
        order_id, INSTRUMENT, side, quantity, quantity if filled is None else filled, status
    )
    fill = lr.FillRow(fill_id, order_id, price, quantity, fee)
    cash = -price * quantity if side == "buy" else price * quantity
    entries = [lr.EntryRow(fill_id, "cash", cash)]
    if fee:
        entries.append(lr.EntryRow(fill_id, "fee", -fee))
    return order, fill, entries


def _rows(trades, positions, realized_entries=()):
    return lr.AccountRows(
        orders=[t[0] for t in trades],
        fills=[t[1] for t in trades],
        entries=[e for t in trades for e in t[2]] + list(realized_entries),
        fill_transaction_refs=[t[1].id for t in trades],
        history={t[0].id: {"submitted", t[0].status} for t in trades},
        positions=positions,
    )


def _position(side, quantity, price, *, realized="0", status="open"):
    return lr.PositionRow(
        uuid4(), INSTRUMENT, side, Decimal(quantity), Decimal(price), Decimal(realized), status
    )


def _codes(rows):
    return sorted({f.code for f in lr.reconcile(rows)})


def _round_trip():
    """Buy 10 @ 100, sell 4 @ 110 (realizes 40): a long of 6 @ 100 is left."""
    buy = _trade("buy", 10, 100, fee="1")
    sell = _trade("sell", 4, 110)
    realized = [lr.EntryRow(sell[1].id, "realized_pnl", Decimal(40))]
    return [buy, sell], [_position("long", 6, 100, realized="40")], realized


def test_consistent_records_have_nothing_to_report() -> None:
    trades, positions, realized = _round_trip()

    assert lr.reconcile(_rows(trades, positions, realized)) == []


def test_a_short_that_was_closed_reconciles() -> None:
    sell = _trade("sell", 5, 150)
    buy = _trade("buy", 5, 140)
    realized = [lr.EntryRow(buy[1].id, "realized_pnl", Decimal(50))]
    closed = _position("short", 0, 150, realized="50", status="closed")

    assert lr.reconcile(_rows([sell, buy], [closed], realized)) == []


def test_a_flip_reconciles() -> None:
    """Long 10 @ 100, then sell 15 @ 110: realizes 100, leaves a short of 5 @ 110."""
    buy = _trade("buy", 10, 100)
    sell = _trade("sell", 15, 110)
    realized = [lr.EntryRow(sell[1].id, "realized_pnl", Decimal(100))]

    assert (
        lr.reconcile(_rows([buy, sell], [_position("short", 5, 110, realized="100")], realized))
        == []
    )


def test_an_account_that_never_traded_has_nothing_to_report() -> None:
    assert lr.reconcile(_rows([], [])) == []


def test_a_fill_without_its_ledger_transaction_is_found() -> None:
    trades, positions, realized = _round_trip()
    rows = replace(
        _rows(trades, positions, realized), fill_transaction_refs=[trades[0][1].id]
    )  # the sell has none

    assert "fill_without_ledger" in _codes(rows)


def test_a_cash_entry_that_does_not_match_the_fill_is_found() -> None:
    trades, positions, realized = _round_trip()
    rows = _rows(trades, positions, realized)
    wrong = [
        lr.EntryRow(e.fill_id, e.entry_type, e.amount + 1) if e.entry_type == "cash" else e
        for e in rows.entries
    ]

    assert "ledger_cash_mismatch" in _codes(replace(rows, entries=wrong))


def test_a_fee_that_does_not_match_the_fill_is_found() -> None:
    trades, positions, realized = _round_trip()
    rows = _rows(trades, positions, realized)
    no_fee = [e for e in rows.entries if e.entry_type != "fee"]

    assert "ledger_fee_mismatch" in _codes(replace(rows, entries=no_fee))


def test_a_ledger_entry_without_a_fill_is_found() -> None:
    trades, positions, realized = _round_trip()
    rows = _rows(trades, positions, realized + [lr.EntryRow(None, "cash", Decimal(5))])

    assert "ledger_entry_without_fill" in _codes(rows)


def test_a_ledger_transaction_whose_fill_is_gone_is_found() -> None:
    trades, positions, realized = _round_trip()
    rows = _rows(trades, positions, realized)

    assert "ledger_transaction_without_fill" in _codes(
        replace(rows, fill_transaction_refs=[*rows.fill_transaction_refs, uuid4()])
    )


def test_an_order_whose_filled_quantity_is_not_its_fills_is_found() -> None:
    trades, positions, realized = _round_trip()
    trades[0] = (replace(trades[0][0], filled_quantity=Decimal(3)), *trades[0][1:])

    assert "order_filled_quantity_mismatch" in _codes(_rows(trades, positions, realized))


def test_a_filled_order_that_is_not_filled_completely_is_found() -> None:
    partial = _trade("buy", 10, 100)
    partial = (replace(partial[0], quantity=Decimal(12)), *partial[1:])

    assert "order_filled_incompletely" in _codes(_rows([partial], [_position("long", 10, 100)]))


def test_a_status_the_history_never_recorded_is_found_but_no_history_is_skipped() -> None:
    trades, positions, realized = _round_trip()
    rows = _rows(trades, positions, realized)
    order_id = trades[0][0].id

    assert "order_status_not_in_history" in _codes(
        replace(rows, history={**rows.history, order_id: {"submitted"}})
    )
    legacy = {k: v for k, v in rows.history.items() if k != order_id}
    assert lr.reconcile(replace(rows, history=legacy)) == []  # an order from before the history


def test_two_open_positions_for_one_instrument_are_found() -> None:
    trades, positions, realized = _round_trip()

    assert "multiple_open_positions" in _codes(
        _rows(trades, [*positions, _position("long", 1, 100)], realized)
    )


def test_a_position_quantity_that_is_not_the_net_of_the_fills_is_found() -> None:
    trades, _, realized = _round_trip()

    assert "position_quantity_mismatch" in _codes(
        _rows(trades, [_position("long", 7, 100, realized="40")], realized)
    )


def test_a_position_on_the_wrong_side_is_found() -> None:
    trades, _, realized = _round_trip()

    assert "position_quantity_mismatch" in _codes(
        _rows(trades, [_position("short", 6, 100, realized="40")], realized)
    )


def test_a_position_that_should_be_closed_but_is_open_is_found() -> None:
    sell = _trade("sell", 5, 150)
    buy = _trade("buy", 5, 140)
    realized = [lr.EntryRow(buy[1].id, "realized_pnl", Decimal(50))]

    assert "position_quantity_mismatch" in _codes(
        _rows([sell, buy], [_position("short", 5, 150, realized="50")], realized)
    )


def test_realized_pnl_that_differs_from_the_ledger_is_found() -> None:
    trades, _, realized = _round_trip()

    assert "realized_pnl_mismatch" in _codes(
        _rows(trades, [_position("long", 6, 100, realized="35")], realized)
    )


def test_an_average_entry_price_the_fills_do_not_support_is_found() -> None:
    trades, _, realized = _round_trip()

    assert _codes(_rows(trades, [_position("long", 6, 90, realized="40")], realized)) == [
        "position_cost_basis_mismatch"
    ]


def test_an_open_position_without_a_quantity_or_price_is_found() -> None:
    trades, _, realized = _round_trip()
    no_price = replace(_position("long", 6, 100, realized="40"), average_entry_price=None)

    assert "open_position_without_entry_price" in _codes(_rows(trades, [no_price], realized))


def test_rounding_inside_the_tolerance_is_not_a_mismatch() -> None:
    trades, positions, realized = _round_trip()
    rows = _rows(trades, positions, realized)
    nudged = [
        lr.EntryRow(e.fill_id, e.entry_type, e.amount + Decimal("0.0000000001"))
        if e.entry_type == "cash"
        else e
        for e in rows.entries
    ]

    assert lr.reconcile(replace(rows, entries=nudged)) == []


def test_one_broken_account_does_not_flood_the_report() -> None:
    trades = [_trade("buy", 1, 100 + i) for i in range(80)]
    rows = replace(_rows(trades, []), fill_transaction_refs=[])

    assert len(lr.reconcile(rows)) == lr.MAX_FINDINGS


def test_instruments_are_reconciled_separately() -> None:
    trades, positions, realized = _round_trip()
    other = uuid4()
    foreign = _trade("buy", 3, 50)
    foreign = (replace(foreign[0], instrument_id=other), *foreign[1:])
    foreign_position = replace(_position("long", 3, 50), instrument_id=other)

    assert lr.reconcile(_rows([*trades, foreign], [*positions, foreign_position], realized)) == []
    assert "position_quantity_mismatch" in _codes(
        _rows(
            [*trades, foreign],
            [*positions, replace(foreign_position, quantity=Decimal(2))],
            realized,
        )
    )


def test_the_summary_for_a_message_is_short() -> None:
    findings = [lr.Finding(f"c{i}", f"問題{i}") for i in range(5)]

    assert ledger_check.findings_summary(findings) == "問題0、問題1、問題2(ほか2件)"


def test_a_closed_position_that_still_holds_a_quantity_is_found() -> None:
    sell = _trade("sell", 5, 150)
    buy = _trade("buy", 5, 140)
    realized = [lr.EntryRow(buy[1].id, "realized_pnl", Decimal(50))]
    closed = _position("short", 3, 150, realized="50", status="closed")

    assert "closed_position_with_quantity" in _codes(_rows([sell, buy], [closed], realized))
