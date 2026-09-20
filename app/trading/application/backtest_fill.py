"""Backtest-only fill simulation (docs/plans/horizon4-lite-backtest.md Unit 3).

Mirrors `order_flow.py`'s fill-price/fee/position-update *formulas* as pure
functions over an in-memory `BacktestPosition`, instead of DB-backed
`TradingPosition`/`Fill`/`LedgerEntry` rows. Per the plan's Unit 3 design decision
(see also `docs/decisions/0003-horizon4-lite-backtest-before-chronos.md`), the
replay harness (Unit 4) never calls `order_flow.place_order`: that function writes
directly into a real `TradingAccount`'s `trading_position`/`ledger_entry` rows, and
reusing it for a historical replay would corrupt live Paper Trading state (and
`trading_account.mode`'s CHECK constraint has no `backtest` value to isolate a
dedicated account for it anyway). This module is called instead, and its outputs
(`SimulatedFill`, `FillOutcome`) are what the replay harness turns into
`backtest_trade` rows -- no `trading_position`/`ledger_entry`/`fill` row is ever
written for a backtest.

The fee formula and slippage direction below are copied verbatim from
`order_flow._fee_buffer`/`_simulate_fill`/`_apply_fill_to_position`, not imported:
those versions take a live `Session` (to read `InstrumentSpread`/`TradingPosition`
rows), which this module must not depend on. Keeping the *numbers* identical is
what makes a backtest run comparable to the live pipeline; the DB access is exactly
what backtest must not share.

**Spread/slippage approximation (ADR 0003, known limitation)**: unlike the live
path, which reads `instrument_spread`'s *current* row per fill, the replay harness
supplies a single `expected_slippage` value up front for an entire run (there is no
historical tick/spread data to look up per bar). Backtest P&L is only as accurate
as that approximation.

`BacktestPosition` does not carry a cumulative `realized_pnl` field the way
`TradingPosition` does: that field exists on the DB row for account-level
reporting, but a backtest's per-fill `realized_pnl` is already captured in the
`backtest_trade` row the replay harness writes for that fill, so there is nothing
here that needs to accumulate it.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

OrderSide = Literal["buy", "sell"]
PositionSide = Literal["long", "short"]


class BacktestFillError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def fee_buffer(exchange_code: str, price: Decimal, quantity: Decimal) -> Decimal:
    """Same formula as `order_flow._fee_buffer` (OANDA=0, Binance=notional*0.1%)."""
    if exchange_code == "oanda":
        return Decimal(0)
    if exchange_code == "binance":
        return price * quantity * Decimal("0.001")
    raise BacktestFillError(
        "unsupported_exchange", f"No fee_buffer defined for exchange '{exchange_code}'"
    )


@dataclass(frozen=True)
class SimulatedFill:
    price: Decimal
    quantity: Decimal
    fee_amount: Decimal


def simulate_fill(
    *,
    exchange_code: str,
    side: OrderSide,
    candle_close: Decimal,
    quantity: Decimal,
    expected_slippage: Decimal,
) -> SimulatedFill:
    """Same fill-price rule as `order_flow._simulate_fill`: a buy fills above the
    candle's close by `expected_slippage`, a sell fills below it."""
    fill_price = (
        candle_close + expected_slippage if side == "buy" else candle_close - expected_slippage
    )
    if fill_price <= 0:
        raise BacktestFillError("invalid_fill_price", "Simulated fill price is not positive")
    return SimulatedFill(
        price=fill_price,
        quantity=quantity,
        fee_amount=fee_buffer(exchange_code, candle_close, quantity),
    )


@dataclass(frozen=True)
class BacktestPosition:
    """In-memory equivalent of one open `trading_position` row. Not persisted --
    the replay harness threads this through successive `apply_fill_to_position`
    calls for one backtest run."""

    side: PositionSide
    quantity: Decimal
    average_entry_price: Decimal


@dataclass(frozen=True)
class FillOutcome:
    position: BacktestPosition | None
    """None when the fill exactly closes the position (no position remains)."""
    realized_pnl: Decimal
    """0 for an opening/increasing fill; the booked P&L for the closed portion of a
    reducing or flipping fill."""


def apply_fill_to_position(
    position: BacktestPosition | None,
    fill: SimulatedFill,
    order_side: OrderSide,
    *,
    allow_short: bool,
) -> FillOutcome:
    """Same net-position accounting as `order_flow._apply_fill_to_position` (one
    position per instrument; open, increase, reduce, exactly close, or flip in one
    fill), operating on a `BacktestPosition` value instead of a DB row. `allow_short`
    mirrors that function's `exchange_code == "oanda"` check (Binance stays
    long-only, per conservative-v1's Binance-specific no-short rule)."""
    opening_side: PositionSide = "long" if order_side == "buy" else "short"

    if position is None:
        if opening_side == "short" and not allow_short:
            raise BacktestFillError(
                "short_not_supported",
                "Opening a short position is not supported for this exchange",
            )
        return FillOutcome(
            position=BacktestPosition(
                side=opening_side, quantity=fill.quantity, average_entry_price=fill.price
            ),
            realized_pnl=Decimal(0),
        )

    same_direction = opening_side == position.side
    if same_direction:
        total_cost = position.average_entry_price * position.quantity + fill.price * fill.quantity
        new_quantity = position.quantity + fill.quantity
        return FillOutcome(
            position=replace(
                position, quantity=new_quantity, average_entry_price=total_cost / new_quantity
            ),
            realized_pnl=Decimal(0),
        )

    # Opposite direction: reduce, exactly close, or flip.
    direction_sign = Decimal(1) if position.side == "long" else Decimal(-1)
    closing_quantity = min(fill.quantity, position.quantity)
    realized_pnl = (fill.price - position.average_entry_price) * closing_quantity * direction_sign

    remainder = fill.quantity - position.quantity
    if remainder > 0:
        if not allow_short:
            raise BacktestFillError(
                "short_not_supported",
                "Fill quantity exceeds the held long position; opening a short "
                "position is not supported for this exchange",
            )
        return FillOutcome(
            position=BacktestPosition(
                side=opening_side, quantity=remainder, average_entry_price=fill.price
            ),
            realized_pnl=realized_pnl,
        )
    if remainder == 0:
        return FillOutcome(position=None, realized_pnl=realized_pnl)

    return FillOutcome(
        position=replace(position, quantity=position.quantity - fill.quantity),
        realized_pnl=realized_pnl,
    )
