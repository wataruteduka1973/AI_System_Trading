"""Computes and records `equity` for a paper trading account, feeding the Risk Gate
(`risk_gate.py`, `risk_budget = equity * risk_per_trade`) and `account_snapshot`
(a time series the Risk Gate queries for daily/weekly loss, peak drawdown, etc.).

`compute_equity` = cash balance + unrealized P&L of the one open position (if any),
both in the instrument's `quote_asset`. Cash balance sums only the `cash`/`fee`/
`deposit`/`withdrawal` `ledger_entry` rows -- **not** `realized_pnl` rows. This is
deliberate, not an oversight: `order_flow.py`'s `_record_ledger` already books the
full sale/purchase proceeds as a `cash` entry (e.g. selling to close a profitable long
adds the full sale proceeds to cash), so a fully round-tripped trade's cash entries
alone already reflect the profit -- the separate `realized_pnl` entry is a redundant
*reporting* annotation of the same event, not an additional movement of money.
Summing it into the cash balance too would double-count every realized gain/loss.

Scoped to one instrument per account (matches the current one-`TradingBot`-per-
instrument shape of this codebase): a `TradingAccount` holding positions in more than
one instrument is out of scope for this function.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.trading import AccountSnapshot, LedgerEntry, TradingAccount, TradingPosition

_CASH_ENTRY_TYPES = ("cash", "fee", "deposit", "withdrawal")


def _cash_balance(db: Session, account: TradingAccount, asset: str) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account.id,
            LedgerEntry.asset == asset,
            LedgerEntry.entry_type.in_(_CASH_ENTRY_TYPES),
        )
    )
    return Decimal(total) if total is not None else Decimal(0)


def _unrealized_pnl(db: Session, account: TradingAccount, instrument: Instrument) -> Decimal:
    position = db.scalar(
        select(TradingPosition).where(
            TradingPosition.account_id == account.id,
            TradingPosition.instrument_id == instrument.id,
            TradingPosition.status == "open",
        )
    )
    if position is None or position.average_entry_price is None:
        return Decimal(0)
    candle = db.scalar(
        select(Candle)
        .where(Candle.instrument_id == instrument.id, Candle.is_final.is_(True))
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if candle is None:
        return Decimal(0)
    direction = Decimal(1) if position.side == "long" else Decimal(-1)
    return (candle.close - position.average_entry_price) * position.quantity * direction


def compute_equity(db: Session, account: TradingAccount, instrument: Instrument) -> Decimal:
    # Evaluation order matches record_account_snapshot's (unrealized first, then cash) --
    # deliberately consistent between the two so tests/callers can rely on one call order.
    unrealized = _unrealized_pnl(db, account, instrument)
    cash = _cash_balance(db, account, instrument.quote_asset)
    return cash + unrealized


def record_account_snapshot(
    db: Session,
    account: TradingAccount,
    instrument: Instrument,
    *,
    source: str = "paper_ledger",
) -> AccountSnapshot:
    """Compute and persist one `AccountSnapshot` row. Does not commit -- callers
    (e.g. `order_flow.place_order`) decide the transaction boundary."""
    now = datetime.now(UTC)
    unrealized = _unrealized_pnl(db, account, instrument)
    cash = _cash_balance(db, account, instrument.quote_asset)
    equity = cash + unrealized
    snapshot = AccountSnapshot(
        account_id=account.id,
        captured_at=now,
        balances={instrument.quote_asset: str(cash)},
        equity=equity,
        unrealized_pnl=unrealized,
        source=source,
    )
    db.add(snapshot)
    db.flush()
    return snapshot
