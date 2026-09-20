"""Unit 3 (docs/plans/horizon4-lite-backtest.md): pure fill/position simulation for
backtest replay. Expected values for the position-update cases are copied from
tests/test_order_flow.py's `_apply_fill_to_position` tests (same formulas, same
numbers) to confirm this module matches the live pipeline's math exactly.
"""

from decimal import Decimal

import pytest
from app.trading.application import backtest_fill as bf

# ---- fee_buffer ----


def test_fee_buffer_oanda_is_zero() -> None:
    assert bf.fee_buffer("oanda", Decimal("150"), Decimal("1000")) == Decimal(0)


def test_fee_buffer_binance_is_notional_times_rate() -> None:
    assert bf.fee_buffer("binance", Decimal("1000000"), Decimal("1")) == Decimal("1000.000")


def test_fee_buffer_unsupported_exchange_raises() -> None:
    with pytest.raises(bf.BacktestFillError) as exc:
        bf.fee_buffer("dydx", Decimal("1"), Decimal("1"))
    assert exc.value.code == "unsupported_exchange"


# ---- simulate_fill ----


def test_simulate_fill_buy_adds_slippage_above_close() -> None:
    fill = bf.simulate_fill(
        exchange_code="oanda",
        side="buy",
        candle_close=Decimal("150"),
        quantity=Decimal("10"),
        expected_slippage=Decimal("0.5"),
    )
    assert fill.price == Decimal("150.5")
    assert fill.quantity == Decimal("10")
    assert fill.fee_amount == Decimal(0)


def test_simulate_fill_sell_subtracts_slippage_below_close() -> None:
    fill = bf.simulate_fill(
        exchange_code="oanda",
        side="sell",
        candle_close=Decimal("150"),
        quantity=Decimal("10"),
        expected_slippage=Decimal("0.5"),
    )
    assert fill.price == Decimal("149.5")


def test_simulate_fill_includes_binance_fee() -> None:
    fill = bf.simulate_fill(
        exchange_code="binance",
        side="buy",
        candle_close=Decimal("1000000"),
        quantity=Decimal("1"),
        expected_slippage=Decimal(0),
    )
    assert fill.fee_amount == Decimal("1000.000")


def test_simulate_fill_rejects_a_non_positive_price() -> None:
    with pytest.raises(bf.BacktestFillError) as exc:
        bf.simulate_fill(
            exchange_code="oanda",
            side="sell",
            candle_close=Decimal("0.3"),
            quantity=Decimal("1"),
            expected_slippage=Decimal("0.5"),
        )
    assert exc.value.code == "invalid_fill_price"


# ---- apply_fill_to_position: opening ----


def test_apply_fill_to_position_opens_a_long_from_none() -> None:
    fill = bf.SimulatedFill(price=Decimal("100"), quantity=Decimal("10"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(None, fill, "buy", allow_short=True)
    assert outcome.realized_pnl == Decimal(0)
    assert outcome.position == bf.BacktestPosition(
        side="long", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )


def test_apply_fill_to_position_opening_short_requires_allow_short() -> None:
    fill = bf.SimulatedFill(price=Decimal("100"), quantity=Decimal("10"), fee_amount=Decimal(0))
    with pytest.raises(bf.BacktestFillError) as exc:
        bf.apply_fill_to_position(None, fill, "sell", allow_short=False)
    assert exc.value.code == "short_not_supported"


# ---- apply_fill_to_position: weighted average on a second buy ----


def test_apply_fill_to_position_weighted_average_on_increase() -> None:
    position = bf.BacktestPosition(
        side="long", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("120"), quantity=Decimal("10"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(position, fill, "buy", allow_short=True)
    assert outcome.realized_pnl == Decimal(0)
    assert outcome.position is not None
    assert outcome.position.quantity == Decimal("20")
    assert outcome.position.average_entry_price == Decimal("110")  # (100*10 + 120*10) / 20


# ---- apply_fill_to_position: short side ----


def test_apply_fill_to_position_short_increases_with_weighted_average() -> None:
    position = bf.BacktestPosition(
        side="short", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("90"), quantity=Decimal("10"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(position, fill, "sell", allow_short=True)
    assert outcome.realized_pnl == Decimal(0)
    assert outcome.position is not None
    assert outcome.position.side == "short"
    assert outcome.position.quantity == Decimal("20")
    assert outcome.position.average_entry_price == Decimal("95")  # (100*10 + 90*10) / 20


def test_apply_fill_to_position_short_reduces_with_profit_when_price_drops() -> None:
    position = bf.BacktestPosition(
        side="short", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("80"), quantity=Decimal("4"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(position, fill, "buy", allow_short=True)
    assert outcome.realized_pnl == Decimal("80")  # (100-80)*4
    assert outcome.position is not None
    assert outcome.position.side == "short"
    assert outcome.position.quantity == Decimal("6")


def test_apply_fill_to_position_short_fully_closes_on_exact_buy() -> None:
    position = bf.BacktestPosition(
        side="short", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("110"), quantity=Decimal("10"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(position, fill, "buy", allow_short=True)
    assert outcome.realized_pnl == Decimal("-100")  # (100-110)*10
    assert outcome.position is None


# ---- apply_fill_to_position: flips ----


def test_apply_fill_to_position_flips_long_to_short_in_one_fill() -> None:
    position = bf.BacktestPosition(
        side="long", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("90"), quantity=Decimal("15"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(position, fill, "sell", allow_short=True)
    assert outcome.realized_pnl == Decimal("-100")  # closing the long: (90-100)*10
    assert outcome.position is not None
    assert outcome.position.side == "short"
    assert outcome.position.quantity == Decimal("5")
    assert outcome.position.average_entry_price == Decimal("90")


def test_apply_fill_to_position_flips_short_to_long_in_one_fill() -> None:
    position = bf.BacktestPosition(
        side="short", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("105"), quantity=Decimal("15"), fee_amount=Decimal(0))
    outcome = bf.apply_fill_to_position(position, fill, "buy", allow_short=True)
    assert outcome.realized_pnl == Decimal("-50")  # closing the short: (100-105)*10
    assert outcome.position is not None
    assert outcome.position.side == "long"
    assert outcome.position.quantity == Decimal("5")
    assert outcome.position.average_entry_price == Decimal("105")


def test_apply_fill_to_position_binance_never_flips() -> None:
    position = bf.BacktestPosition(
        side="long", quantity=Decimal("10"), average_entry_price=Decimal("100")
    )
    fill = bf.SimulatedFill(price=Decimal("90"), quantity=Decimal("15"), fee_amount=Decimal(0))
    with pytest.raises(bf.BacktestFillError) as exc:
        bf.apply_fill_to_position(position, fill, "sell", allow_short=False)
    assert exc.value.code == "short_not_supported"
    # No flip happened: this module is pure, so the caller's original `position`
    # value is untouched regardless (nothing to assert beyond the raise itself).
