from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.trading import AccountSnapshot, TradingAccount, TradingPosition
from app.trading.application.account_valuation import compute_equity, record_account_snapshot


def _instrument() -> Instrument:
    return Instrument(
        id=uuid4(),
        exchange_id=uuid4(),
        market_id=uuid4(),
        symbol="USD_JPY",
        base_asset="USD",
        quote_asset="JPY",
        price_scale=3,
        quantity_scale=0,
        tick_size=Decimal("0.001"),
        step_size=Decimal("1"),
    )


def _account() -> TradingAccount:
    return TradingAccount(id=uuid4(), workspace_id=uuid4(), mode="paper", base_currency="JPY")


def test_compute_equity_is_cash_only_when_flat() -> None:
    db = MagicMock()
    # compute_equity evaluates unrealized (position lookup) first, then cash
    db.scalar.side_effect = [None, Decimal("1000000")]
    equity = compute_equity(db, _account(), _instrument())
    assert equity == Decimal("1000000")


def test_compute_equity_adds_unrealized_profit_for_open_long() -> None:
    db = MagicMock()
    instrument = _instrument()
    position = TradingPosition(
        id=uuid4(),
        account_id=uuid4(),
        instrument_id=instrument.id,
        side="long",
        quantity=Decimal("1000"),
        average_entry_price=Decimal("150"),
        status="open",
    )
    candle = Candle(
        id=uuid4(),
        instrument_id=instrument.id,
        timeframe="1m",
        open_time=datetime.now(UTC),
        close_time=datetime.now(UTC),
        open=Decimal("151"),
        high=Decimal("151"),
        low=Decimal("151"),
        close=Decimal("151"),  # +1 vs entry
        source="test",
        is_final=True,
    )
    db.scalar.side_effect = [position, candle, Decimal("850000")]
    equity = compute_equity(db, _account(), instrument)
    assert equity == Decimal("850000") + Decimal("1000")  # cash + (151-150)*1000


def test_compute_equity_subtracts_unrealized_loss_for_open_short() -> None:
    db = MagicMock()
    instrument = _instrument()
    position = TradingPosition(
        id=uuid4(),
        account_id=uuid4(),
        instrument_id=instrument.id,
        side="short",
        quantity=Decimal("1000"),
        average_entry_price=Decimal("150"),
        status="open",
    )
    candle = Candle(
        id=uuid4(),
        instrument_id=instrument.id,
        timeframe="1m",
        open_time=datetime.now(UTC),
        close_time=datetime.now(UTC),
        open=Decimal("151"),
        high=Decimal("151"),
        low=Decimal("151"),
        close=Decimal("151"),  # price rose -- bad for a short
        source="test",
        is_final=True,
    )
    db.scalar.side_effect = [position, candle, Decimal("850000")]
    equity = compute_equity(db, _account(), instrument)
    assert equity == Decimal("850000") - Decimal("1000")


def test_record_account_snapshot_persists_and_flushes() -> None:
    db = MagicMock()
    db.scalar.side_effect = [None, Decimal("500000"), Decimal("500000")]
    snapshot = record_account_snapshot(db, _account(), _instrument())
    assert isinstance(snapshot, AccountSnapshot)
    assert snapshot.equity == Decimal("500000")
    assert snapshot.unrealized_pnl == Decimal("0")
    db.add.assert_called_once()
    db.flush.assert_called_once()
