from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import RiskProfileVersion, Signal, TradingBot
from app.models.trading import TradingAccount
from app.trading.application import risk_gate as gate


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
        min_quantity=None,
        max_quantity=None,
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def _candle(close: Decimal, i: int, high=None, low=None) -> Candle:
    t = datetime(2026, 9, 20, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        source="test",
        is_final=True,
    )


# ---- pure/near-pure helpers ----


def test_decimal_reads_string_rule_values() -> None:
    assert gate._decimal({"x": "0.5"}, "x") == Decimal("0.5")


def test_fee_buffer_per_unit_oanda_is_zero() -> None:
    assert gate._fee_buffer_per_unit(
        "oanda", Decimal("150"), gate.CONSERVATIVE_V1_RULES
    ) == Decimal(0)


def test_fee_buffer_per_unit_binance_is_price_times_rate() -> None:
    result = gate._fee_buffer_per_unit("binance", Decimal("1000000"), gate.CONSERVATIVE_V1_RULES)
    assert result == Decimal("1000000") * Decimal("0.001")


def test_fee_buffer_per_unit_unsupported_exchange_raises() -> None:
    with pytest.raises(gate.RiskGateError) as exc:
        gate._fee_buffer_per_unit("dydx", Decimal("1"), gate.CONSERVATIVE_V1_RULES)
    assert exc.value.code == "unsupported_exchange"


def test_stop_distance_uses_the_largest_component_oanda() -> None:
    instrument = _instrument(tick_size=Decimal("0.001"))
    # ATR component and spread component both small; broker-min placeholder wins.
    candles = [_candle(Decimal("150"), i) for i in range(20)]
    result = gate._stop_distance(
        "oanda", instrument, candles, Decimal("0.01"), gate.CONSERVATIVE_V1_RULES
    )
    assert result == instrument.tick_size * Decimal(50)


def test_stop_distance_binance_has_no_broker_minimum_component() -> None:
    instrument = _instrument()
    candles = [_candle(Decimal("150"), i) for i in range(20)]
    # Flat candles -> ATR=0, so spread*3 should be the only nonzero component.
    result = gate._stop_distance(
        "binance", instrument, candles, Decimal("100"), gate.CONSERVATIVE_V1_RULES
    )
    assert result == Decimal("100") * Decimal("3.0")


# ---- evaluate_signal: deny paths ----


def _signal() -> Signal:
    return Signal(
        id=uuid4(),
        workspace_id=uuid4(),
        bot_run_id=uuid4(),
        candle_id=uuid4(),
        strategy_version_id=uuid4(),
        action="buy",
        rationale={},
        input_checksum="x",
    )


def _account() -> TradingAccount:
    return TradingAccount(id=uuid4(), workspace_id=uuid4(), mode="paper", base_currency="JPY")


def _bot(timeframe="1m") -> TradingBot:
    return TradingBot(
        id=uuid4(),
        workspace_id=uuid4(),
        name="bot",
        execution_mode="paper",
        strategy_mode="technical",
        connection_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        timeframe=timeframe,
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
    )


def _risk_profile_version() -> RiskProfileVersion:
    return RiskProfileVersion(
        id=uuid4(),
        risk_profile_id=uuid4(),
        version=1,
        rules=gate.CONSERVATIVE_V1_RULES,
        checksum="x",
        status="approved",
    )


def test_evaluate_signal_rejects_non_buy_sell_action() -> None:
    db = MagicMock()
    signal = _signal()
    signal.action = "hold"
    with pytest.raises(gate.RiskGateError) as exc:
        gate.evaluate_signal(
            db, signal, _account(), _instrument(), _bot(), _risk_profile_version(), "oanda"
        )
    assert exc.value.code == "invalid_signal_action"


def test_evaluate_signal_denies_with_no_equity() -> None:
    db = MagicMock()
    instrument = _instrument()
    candles = [_candle(Decimal("150"), i) for i in range(20)]
    # _recent_final_candles -> db.scalars(...).all(); everything else via db.scalar/db.get
    db.scalars.return_value.all.return_value = candles
    db.get.return_value = None  # no InstrumentSpread row -> spread=0
    # compute_equity: _unrealized_pnl (position=None) then _cash_balance (0)
    db.scalar.side_effect = [None, Decimal("0")]

    result = gate.evaluate_signal(
        db, _signal(), _account(), instrument, _bot(), _risk_profile_version(), "oanda"
    )
    assert result.decision.outcome == "deny"
    assert result.approved_quantity is None
    assert result.decision.rule_results["equity"]["passed"] is False
