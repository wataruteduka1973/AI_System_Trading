"""Minimal paper-account funding: records a deposit so `equity` is a real, non-zero
number instead of undefined.

Nothing in this codebase creates `ledger_entry(entry_type='deposit')` rows anywhere
else -- there is no account-funding UI/flow at all yet, paper or otherwise. The Risk
Gate (`app/trading/application/risk_gate.py`) needs `equity` to size positions
(`risk_budget = equity * risk_per_trade`), so this module exists to seed a starting
balance for a paper account. The amount is a **placeholder** the caller must choose
deliberately (there is no product decision yet on what a paper account should start
with); this module does not pick one on its own.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.trading import LedgerEntry, LedgerTransaction, TradingAccount


def seed_paper_deposit(
    db: Session,
    account: TradingAccount,
    *,
    amount: Decimal,
    asset: str,
    note: str = "paper account seed deposit",
) -> LedgerTransaction:
    """Record a deposit of `amount` `asset` into `account`. Caller commits its own
    transaction (matches how `order_flow.py`'s private helpers are composed by their
    callers, not self-contained transactions)."""
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    now = datetime.now(UTC)
    transaction = LedgerTransaction(
        account_id=account.id,
        reference_type="deposit",
        reference_id=None,
        description=note,
        occurred_at=now,
    )
    db.add(transaction)
    db.flush()
    db.add(
        LedgerEntry(
            transaction_id=transaction.id,
            account_id=account.id,
            fill_id=None,
            asset=asset,
            amount=amount,
            entry_type="deposit",
            occurred_at=now,
        )
    )
    db.flush()
    return transaction
