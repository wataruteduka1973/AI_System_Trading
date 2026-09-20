from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.trading import LedgerEntry, LedgerTransaction, TradingAccount
from app.trading.application.account_funding import seed_paper_deposit


def _account() -> TradingAccount:
    return TradingAccount(id=uuid4(), workspace_id=uuid4(), mode="paper", base_currency="JPY")


def test_seed_paper_deposit_rejects_non_positive_amount() -> None:
    db = MagicMock()
    with pytest.raises(ValueError):
        seed_paper_deposit(db, _account(), amount=Decimal("0"), asset="JPY")


def test_seed_paper_deposit_creates_transaction_and_deposit_entry() -> None:
    db = MagicMock()
    transaction = seed_paper_deposit(db, _account(), amount=Decimal("1000000"), asset="JPY")
    assert isinstance(transaction, LedgerTransaction)
    assert transaction.reference_type == "deposit"

    added = [call.args[0] for call in db.add.call_args_list]
    entries = [obj for obj in added if isinstance(obj, LedgerEntry)]
    assert len(entries) == 1
    assert entries[0].entry_type == "deposit"
    assert entries[0].amount == Decimal("1000000")
    assert entries[0].asset == "JPY"
