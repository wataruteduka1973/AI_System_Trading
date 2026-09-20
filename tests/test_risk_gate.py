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


# ---- _evaluate_conservative_v1 (pure) ----
#
# Unit 2 (docs/plans/horizon4-lite-backtest.md): these exercise the extracted pure
# function directly with a hand-built RiskState, no Session/MagicMock involved --
# exactly the shape a future backtest replay harness would call it with.


def _state(**overrides: object) -> gate.RiskState:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    defaults: dict[str, object] = dict(
        signal_action="buy",
        now=now,
        rules=gate.CONSERVATIVE_V1_RULES,
        exchange_code="oanda",
        instrument=_instrument(),
        market_price=Decimal("150"),
        latest_candle_close_time=now,
        bar_seconds=60,
        stop_distance=Decimal("10"),
        expected_slippage=Decimal(0),
        fee_buffer_per_unit=Decimal(0),
        equity=Decimal("1000000"),
        existing_open_risk=Decimal(0),
        has_open_position=False,
        day_start_equity=None,
        week_start_equity=None,
        peak_equity=None,
        consecutive_losses=0,
        minutes_since_last_order=None,
    )
    defaults.update(overrides)
    return gate.RiskState(**defaults)


def test_evaluate_conservative_v1_allows_a_healthy_signal() -> None:
    result = gate._evaluate_conservative_v1(_state())
    assert result.outcome == "allow"
    assert result.approved_quantity == Decimal("500")
    assert result.reason_code is None


def test_evaluate_conservative_v1_is_a_pure_function_of_its_state() -> None:
    state = _state()
    first = gate._evaluate_conservative_v1(state)
    second = gate._evaluate_conservative_v1(state)
    assert first == second


def test_evaluate_conservative_v1_denies_on_order_interval_hard_breach() -> None:
    result = gate._evaluate_conservative_v1(_state(minutes_since_last_order=Decimal("0.5")))
    assert result.outcome == "deny"
    assert result.approved_quantity is None
    assert result.reason_code == "hard_limit_breach"
    assert result.rule_results["order_interval"]["passed"] is False


def test_evaluate_conservative_v1_denies_binance_sell_with_no_open_position() -> None:
    result = gate._evaluate_conservative_v1(
        _state(exchange_code="binance", signal_action="sell", has_open_position=False)
    )
    assert result.outcome == "deny"
    assert result.rule_results["binance_no_short"]["passed"] is False


def test_evaluate_conservative_v1_allows_binance_sell_that_closes_a_position() -> None:
    result = gate._evaluate_conservative_v1(
        _state(exchange_code="binance", signal_action="sell", has_open_position=True)
    )
    assert result.rule_results["binance_no_short"]["passed"] is True


def test_evaluate_conservative_v1_adjusts_quantity_down_to_broker_limit() -> None:
    small_max = _instrument(max_quantity=Decimal("100"))
    result = gate._evaluate_conservative_v1(_state(instrument=small_max))
    assert result.outcome == "allow_with_adjustment"
    assert result.approved_quantity == Decimal("100")
