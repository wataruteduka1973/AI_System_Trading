import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.audit import AuditLog
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import RiskDecision, Signal
from app.models.trading import (
    Fill,
    LedgerEntry,
    OrderIntent,
    TradeOrder,
    TradingAccount,
    TradingPosition,
)
from app.trading.application import order_flow as flow


def test_application_has_no_http_or_response_schema_imports() -> None:
    tree = ast.parse(inspect.getsource(flow))
    modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(
        module and module.startswith(("fastapi", "app.api", "app.schemas")) for module in modules
    )


def _instrument(**overrides: object) -> Instrument:
    defaults: dict[str, object] = dict(
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
    defaults.update(overrides)
    return Instrument(**defaults)


def _candle(close: Decimal, **overrides: object) -> Candle:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=datetime.now(UTC),
        close_time=datetime.now(UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        source="test",
        is_final=True,
    )
    defaults.update(overrides)
    return Candle(**defaults)


def _account(**overrides: object) -> TradingAccount:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        connection_id=uuid4(),
        mode="paper",
        base_currency="JPY",
    )
    defaults.update(overrides)
    return TradingAccount(**defaults)


def _position(**overrides: object) -> TradingPosition:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        side="long",
        quantity=Decimal("10"),
        average_entry_price=Decimal("100"),
        realized_pnl=Decimal("0"),
        status="open",
        version=1,
    )
    defaults.update(overrides)
    return TradingPosition(**defaults)


def _fill(**overrides: object) -> Fill:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        order_id=uuid4(),
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee_amount=Decimal("0"),
        fee_asset="JPY",
        executed_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Fill(**defaults)


# ---- _validate_order_type_price ----


def test_validate_rejects_market_order_with_limit_price() -> None:
    with pytest.raises(flow.OrderFlowError) as exc:
        flow._validate_order_type_price("market", Decimal("100"))
    assert exc.value.code == "invalid_input"


def test_validate_rejects_limit_order_without_limit_price() -> None:
    with pytest.raises(flow.OrderFlowError) as exc:
        flow._validate_order_type_price("limit", None)
    assert exc.value.code == "invalid_input"


def test_validate_accepts_matching_combinations() -> None:
    flow._validate_order_type_price("market", None)
    flow._validate_order_type_price("limit", Decimal("100"))


# ---- create_order_intent ----


def test_create_order_intent_rejects_non_positive_quantity() -> None:
    db = MagicMock()
    command = flow.OrderIntentCommand(
        signal_id=uuid4(),
        risk_decision_id=uuid4(),
        side="buy",
        order_type="market",
        requested_quantity=Decimal("0"),
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.create_order_intent(db, command)
    assert exc.value.code == "invalid_input"
    db.commit.assert_not_called()


def test_create_order_intent_requires_matching_signal() -> None:
    db = MagicMock()
    signal_id, risk_decision_id = uuid4(), uuid4()
    signal = Signal(id=signal_id)
    risk_decision = RiskDecision(id=risk_decision_id, signal_id=uuid4())  # mismatched
    db.get.side_effect = [signal, risk_decision]
    command = flow.OrderIntentCommand(
        signal_id=signal_id,
        risk_decision_id=risk_decision_id,
        side="buy",
        order_type="market",
        requested_quantity=Decimal("1"),
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.create_order_intent(db, command)
    assert exc.value.code == "risk_decision_signal_mismatch"
    db.rollback.assert_called_once()


def test_create_order_intent_persists_and_commits() -> None:
    db = MagicMock()
    signal_id, risk_decision_id = uuid4(), uuid4()
    signal = Signal(id=signal_id)
    risk_decision = RiskDecision(id=risk_decision_id, signal_id=signal_id)
    db.get.side_effect = [signal, risk_decision]
    command = flow.OrderIntentCommand(
        signal_id=signal_id,
        risk_decision_id=risk_decision_id,
        side="buy",
        order_type="market",
        requested_quantity=Decimal("100"),
    )
    result = flow.create_order_intent(db, command)
    assert isinstance(result, OrderIntent)
    assert result.signal_id == signal_id
    assert result.risk_decision_id == risk_decision_id
    db.commit.assert_called_once()


# ---- place_order: validation / lookup failures (no db chain needed) ----


def test_place_order_rejects_non_positive_quantity() -> None:
    db = MagicMock()
    command = flow.PlaceOrderCommand(
        workspace_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        side="buy",
        order_type="market",
        quantity=Decimal("0"),
        client_order_id="c1",
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.place_order(db, command)
    assert exc.value.code == "invalid_input"


def test_place_order_account_not_found() -> None:
    db = MagicMock()
    db.scalar.side_effect = [None]
    command = flow.PlaceOrderCommand(
        workspace_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        client_order_id="c1",
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.place_order(db, command)
    assert exc.value.code == "account_not_found"


def test_place_order_instrument_not_found() -> None:
    db = MagicMock()
    db.scalar.side_effect = [_account()]
    db.get.return_value = None
    command = flow.PlaceOrderCommand(
        workspace_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        client_order_id="c1",
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.place_order(db, command)
    assert exc.value.code == "instrument_not_found"


def test_place_order_order_intent_side_mismatch() -> None:
    db = MagicMock()
    account = _account()
    instrument = _instrument()
    order_intent = OrderIntent(id=uuid4(), side="sell")
    db.scalar.side_effect = [account]
    db.get.side_effect = [instrument, order_intent]
    command = flow.PlaceOrderCommand(
        workspace_id=uuid4(),
        account_id=account.id,
        instrument_id=instrument.id,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        client_order_id="c1",
        order_intent_id=order_intent.id,
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.place_order(db, command)
    assert exc.value.code == "order_intent_mismatch"


# ---- place_order: full happy path (buy open, sell close) ----


def test_place_order_buy_opens_position_and_records_ledger() -> None:
    db = MagicMock()
    account = _account()
    instrument = _instrument()
    candle = _candle(Decimal("150.000"), instrument_id=instrument.id)
    db.scalar.side_effect = [account, candle, "oanda", None]
    db.get.return_value = instrument
    command = flow.PlaceOrderCommand(
        workspace_id=account.workspace_id,
        account_id=account.id,
        instrument_id=instrument.id,
        side="buy",
        order_type="market",
        quantity=Decimal("1000"),
        client_order_id="c1",
    )
    order = flow.place_order(db, command)
    assert order.status == "filled"
    assert order.filled_quantity == Decimal("1000")

    added = [call.args[0] for call in db.add.call_args_list]
    fills = [obj for obj in added if isinstance(obj, Fill)]
    positions = [obj for obj in added if isinstance(obj, TradingPosition)]
    ledger_entries = [obj for obj in added if isinstance(obj, LedgerEntry)]
    audits = [obj for obj in added if isinstance(obj, AuditLog)]

    assert len(fills) == 1
    assert fills[0].price == Decimal("150.000")  # slippage=0 for now
    assert fills[0].fee_amount == Decimal("0")  # oanda fee_buffer=0

    assert len(positions) == 1
    assert positions[0].quantity == Decimal("1000")
    assert positions[0].average_entry_price == Decimal("150.000")

    # opening buy: cash entry only, no fee entry (fee=0), no realized_pnl entry
    assert {entry.entry_type for entry in ledger_entries} == {"cash"}
    assert ledger_entries[0].amount == -(Decimal("150.000") * Decimal("1000"))

    assert {audit.action for audit in audits} == {"trade_order.submitted", "trade_order.filled"}
    db.commit.assert_called_once()


def test_place_order_sell_closes_position_with_fee_and_realized_pnl() -> None:
    db = MagicMock()
    account = _account()
    instrument = _instrument()
    candle = _candle(Decimal("120"), instrument_id=instrument.id)
    existing_position = _position(
        account_id=account.id,
        instrument_id=instrument.id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
    )
    db.scalar.side_effect = [account, candle, "binance", existing_position]
    db.get.return_value = instrument
    command = flow.PlaceOrderCommand(
        workspace_id=account.workspace_id,
        account_id=account.id,
        instrument_id=instrument.id,
        side="sell",
        order_type="market",
        quantity=Decimal("1"),
        client_order_id="c2",
    )
    flow.place_order(db, command)

    added = [call.args[0] for call in db.add.call_args_list]
    fills = [obj for obj in added if isinstance(obj, Fill)]
    ledger_entries = [obj for obj in added if isinstance(obj, LedgerEntry)]

    assert fills[0].fee_amount == Decimal("120") * Decimal("1") * Decimal("0.001")
    assert existing_position.status == "closed"
    assert existing_position.closed_at is not None
    assert existing_position.realized_pnl == Decimal("20")  # (120-100)*1

    entry_types = {entry.entry_type for entry in ledger_entries}
    assert entry_types == {"cash", "fee", "realized_pnl"}
    realized_entry = next(e for e in ledger_entries if e.entry_type == "realized_pnl")
    assert realized_entry.amount == Decimal("20")


def test_place_order_sell_exceeding_position_is_not_supported() -> None:
    db = MagicMock()
    account = _account()
    instrument = _instrument()
    candle = _candle(Decimal("120"), instrument_id=instrument.id)
    db.scalar.side_effect = [account, candle, "oanda", None]
    db.get.return_value = instrument
    command = flow.PlaceOrderCommand(
        workspace_id=account.workspace_id,
        account_id=account.id,
        instrument_id=instrument.id,
        side="sell",
        order_type="market",
        quantity=Decimal("1"),
        client_order_id="c3",
    )
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.place_order(db, command)
    assert exc.value.code == "short_not_supported"
    db.rollback.assert_called_once()


# ---- _apply_fill_to_position: weighted average on a second buy ----


def test_apply_fill_to_position_weighted_average_on_increase() -> None:
    db = MagicMock()
    account = _account()
    instrument = _instrument()
    existing_position = _position(
        account_id=account.id,
        instrument_id=instrument.id,
        quantity=Decimal("10"),
        average_entry_price=Decimal("100"),
    )
    db.scalar.return_value = existing_position
    fill = _fill(price=Decimal("120"), quantity=Decimal("10"))
    position, realized_pnl = flow._apply_fill_to_position(db, account, instrument, fill, "buy")
    assert realized_pnl == Decimal("0")
    assert position.quantity == Decimal("20")
    assert position.average_entry_price == Decimal("110")  # (100*10 + 120*10) / 20


# ---- _fee_buffer / _exchange_code_for_account ----


def test_fee_buffer_oanda_is_zero() -> None:
    assert flow._fee_buffer("oanda", Decimal("150"), Decimal("1000")) == Decimal("0")


def test_fee_buffer_binance_is_ten_bps_of_notional() -> None:
    assert flow._fee_buffer("binance", Decimal("1000000"), Decimal("1")) == Decimal("1000.000")


def test_fee_buffer_unsupported_exchange_raises() -> None:
    with pytest.raises(flow.OrderFlowError) as exc:
        flow._fee_buffer("dydx", Decimal("1"), Decimal("1"))
    assert exc.value.code == "unsupported_exchange"


def test_exchange_code_for_account_requires_connection() -> None:
    db = MagicMock()
    account = _account(connection_id=None)
    with pytest.raises(flow.OrderFlowError) as exc:
        flow._exchange_code_for_account(db, account)
    assert exc.value.code == "connection_missing"


# ---- cancel_order ----


def test_cancel_order_transitions_from_submitted() -> None:
    db = MagicMock()
    order = TradeOrder(id=uuid4(), workspace_id=uuid4(), status="submitted")
    result = flow.cancel_order(db, order, reason_code="user_requested")
    assert result.status == "cancelled"
    db.commit.assert_called_once()
    audits = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AuditLog)]
    assert audits[0].after_data == {
        "reason_code": "user_requested",
        "previous_status": "submitted",
    }


@pytest.mark.parametrize("status", ["filled", "cancelled", "rejected", "expired"])
def test_cancel_order_rejects_terminal_states(status: str) -> None:
    db = MagicMock()
    order = TradeOrder(id=uuid4(), workspace_id=uuid4(), status=status)
    with pytest.raises(flow.OrderFlowError) as exc:
        flow.cancel_order(db, order, reason_code="user_requested")
    assert exc.value.code == "invalid_status_transition"
    assert order.status == status  # not mutated
    db.rollback.assert_called_once()
