"""HTTP-independent order flow commands: OrderIntent creation, order placement,
self-simulated fill execution, and position/ledger bookkeeping.

See docs/concept/FXtrading_rebuild/08_取引アルゴリズムとリスク初期値.md §5.1 for the
`expected_slippage`/`fee_buffer` draft coefficients this module applies, and the Horizon 3
order-flow implementation plan (2026-09-20) for the design decisions summarized below.

Execution model: paper trading only, fully self-simulated, immediate fill at the latest
final candle's close price (no real exchange order submission -- see FR-ORD-15). There is
no waiting/partial-fill window, so every order created here goes straight to `submitted`
and is filled synchronously within one call to `place_order`; `pending` is the DB default
but is not held as a separate persisted step here because this implementation has no
validation stage between them (risk-gate approval, which the transition table's
`pending -> submitted` trigger refers to, is out of scope -- see the implementation plan).

`expected_slippage` (2026-09-20 update): OANDA now reads the latest persisted bid/ask
(`fx.instrument_spread`, fed by `app/market_data/infrastructure/oanda_stream.py`'s live
PricingStream connection -- see `InstrumentSpread`'s docstring) and applies
`spread * 0.5` per 08_取引アルゴリズムとリスク初期値.md§5.1's draft (unapproved)
coefficient. If no spread row exists yet for the instrument (nothing has streamed it
since this feature shipped, or the DB write raced/failed -- see
`OandaFeedWorker.record_spread`'s docstring), this falls back to `expected_slippage = 0`,
same as before, rather than failing the fill: a missing spread degrades slippage accuracy,
it does not mean the order can't be filled. TODO(binance-spread): Binance still has no
bid/ask concept at all (kline-only adapter) and stays hardcoded to 0. `fee_buffer` from
08_取引アルゴリズムとリスク初期値.md§5.1's draft (unapproved) values does not depend on
spread and is unchanged: OANDA=0, Binance=notional×0.1%.

Known limitation: `instrument_spread` is only populated while the OANDA live stream is
actually running for that instrument (`FeedHub` only starts a feed once a WebSocket
subscriber connects -- see `app/market_data/infrastructure/candle_stream.py`), unlike
`candle`, which a separate polling worker keeps populated regardless of live viewers. If
no one has had that instrument's live chart open recently, `expected_slippage` silently
falls back to 0 exactly as before this change.

Positions (2026-09-20 update): OANDA now supports a **net** position model -- one open
`trading_position` row per (account, instrument), whose `side` toggles between `long` and
`short` as the net direction changes. This reuses the DB's existing `long`/`short` enum
values on `side`; the literal string `'net'` (also a valid DB value) is never written --
"net" describes the *accounting model* (one netted row, not separate hedged long/short
rows), not a `side` value. `quantity` always stays the DB-required `>= 0` magnitude; no
migration was needed. A fill that exceeds the currently held opposite-direction quantity
**flips** the position in one step (closes the existing leg, opens the remainder in the new
direction at the same fill price) rather than being split into two orders -- see
`_apply_fill_to_position`'s docstring for why. Binance keeps the original long-only
behavior unchanged: a sell exceeding the held long quantity still raises
`OrderFlowError("short_not_supported", ...)`, per conservative-v1's Binance-specific
"レバレッジ・借入・空売り: 禁止" rule and Binance spot's inability to short at all.
`_apply_fill_to_position` takes `exchange_code` to select between the two behaviors.

Known asymmetry NOT implemented here (found, not silently added -- see the completion
report): OANDA charges/pays daily financing (rollover/swap) based on the interest-rate
differential between the pair's two currencies, and its sign/magnitude differs between a
long and a short position in the same instrument. `ledger_entry.entry_type` already has a
`financing` value (unused by this module) that is plausibly meant for exactly this. No
formula or data source for it is implemented; conservative-v1's existing risk parameters
(stop distance, position-size-from-risk, leverage cap, margin thresholds) are otherwise
applied identically regardless of direction, since none of them are direction-dependent by
nature -- but neither is Risk Gate itself applied yet (out of scope, see below).

trading_halt (updated, ADR 0004/docs/plans/trading-halt-mvp.md Unit C): `place_order` now
blocks a new/increasing entry when an `entry_halted`-or-more active halt covers the account
(or bot, if `PlaceOrderCommand.bot_id` is given) -- closing/reducing an opposite-direction
position is always allowed. This module still never activates/escalates/releases a halt
itself (that remains `trading_halt.py`'s and `risk_gate.py`'s job); it only reads. Only the
two causes ADR 0004 wired up (data delay, daily/weekly loss·peak drawdown) can ever set one
of these halts today -- the other 8 causes in 05番's 取引停止マトリクス have no detection
code anywhere and so can never block an order here yet.

TODO(order_status_history): the live DB has an `order_status_history` table (order_id,
from_status, to_status, reason_code, raw_ref, occurred_at) with no ORM mapping anywhere in
this codebase yet. This module records lifecycle events via the existing `AuditLog`
mechanism instead (matching every other application module), but does not dual-write
`order_status_history`. Flagged for a future decision on whether that table should be
mapped and populated.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.connections import Exchange, ExchangeConnection
from app.models.instruments import Instrument
from app.models.market_data import Candle, InstrumentSpread
from app.models.strategy import RiskDecision, Signal
from app.models.trading import (
    Fill,
    LedgerEntry,
    LedgerTransaction,
    OrderIntent,
    TradeOrder,
    TradingAccount,
    TradingPosition,
)
from app.trading.application import trading_halt
from app.trading.application.account_valuation import record_account_snapshot

OrderSide = Literal["buy", "sell"]
OrderTypeLiteral = Literal["market", "limit"]

_CANCELLABLE_STATUSES = {"pending", "submitted", "partially_filled"}


class OrderFlowError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@contextmanager
def _rollback_on_failure(db: Session) -> Iterator[None]:
    try:
        yield
    except Exception:
        db.rollback()
        raise


@dataclass(frozen=True)
class OrderIntentCommand:
    signal_id: UUID
    risk_decision_id: UUID
    side: OrderSide
    order_type: OrderTypeLiteral
    requested_quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    max_slippage_bps: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class PlaceOrderCommand:
    workspace_id: UUID
    account_id: UUID
    instrument_id: UUID
    side: OrderSide
    order_type: OrderTypeLiteral
    quantity: Decimal
    client_order_id: str
    order_intent_id: UUID | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "gtc"
    bot_id: UUID | None = None
    """Optional (ADR 0004/docs/plans/trading-halt-mvp.md Unit C): when the caller
    knows which bot this order is for (e.g. `dummy_pipeline.py`), passing it lets
    `place_order` also check a bot-scoped `trading_halt` (data-delay), on top of the
    account-scoped one it always checks. A manual/direct order with no bot behind it
    leaves this `None` and only the account-scoped check applies."""


def _validate_order_type_price(order_type: OrderTypeLiteral, limit_price: Decimal | None) -> None:
    if order_type == "market" and limit_price is not None:
        raise OrderFlowError("invalid_input", "market orders must not set limit_price")
    if order_type == "limit" and (limit_price is None or limit_price <= 0):
        raise OrderFlowError("invalid_input", "limit orders require a positive limit_price")


def create_order_intent(db: Session, command: OrderIntentCommand) -> OrderIntent:
    """Create an OrderIntent from an already-decided Signal/RiskDecision pair. Both are
    mandatory at the DB level (`order_intent.signal_id`/`risk_decision_id` are NOT NULL and
    unique) -- there is no "manual OrderIntent" path; manual/test order placement instead
    calls `place_order` directly with `order_intent_id=None` (see module docstring / the
    implementation plan's investigation notes)."""
    with _rollback_on_failure(db):
        if command.requested_quantity <= 0:
            raise OrderFlowError("invalid_input", "requested_quantity must be greater than 0")
        _validate_order_type_price(command.order_type, command.limit_price)

        signal = db.get(Signal, command.signal_id)
        if signal is None:
            raise OrderFlowError("signal_not_found", "Signal not found")
        risk_decision = db.get(RiskDecision, command.risk_decision_id)
        if risk_decision is None:
            raise OrderFlowError("risk_decision_not_found", "RiskDecision not found")
        if risk_decision.signal_id != command.signal_id:
            raise OrderFlowError(
                "risk_decision_signal_mismatch",
                "RiskDecision does not belong to the given Signal",
            )

        intent = OrderIntent(
            signal_id=command.signal_id,
            risk_decision_id=command.risk_decision_id,
            side=command.side,
            order_type=command.order_type,
            requested_quantity=command.requested_quantity,
            limit_price=command.limit_price,
            stop_price=command.stop_price,
            take_profit_price=command.take_profit_price,
            max_slippage_bps=command.max_slippage_bps,
            expires_at=command.expires_at,
        )
        db.add(intent)
        db.commit()
        db.refresh(intent)
        return intent


def place_order(db: Session, command: PlaceOrderCommand) -> TradeOrder:
    """Create a TradeOrder (optionally from an OrderIntent, otherwise a direct/manual
    order) and synchronously simulate its fill. See module docstring for the execution
    model and its TODOs (spread, trading_halt)."""
    with _rollback_on_failure(db):
        if command.quantity <= 0:
            raise OrderFlowError("invalid_input", "quantity must be greater than 0")
        _validate_order_type_price(command.order_type, command.limit_price)

        account = db.scalar(
            select(TradingAccount).where(
                TradingAccount.id == command.account_id,
                TradingAccount.workspace_id == command.workspace_id,
            )
        )
        if account is None:
            raise OrderFlowError("account_not_found", "Trading account not found")
        instrument = db.get(Instrument, command.instrument_id)
        if instrument is None:
            raise OrderFlowError("instrument_not_found", "Instrument not found")
        exchange_code = _exchange_code_for_account(db, account)

        if command.order_intent_id is not None:
            order_intent = db.get(OrderIntent, command.order_intent_id)
            if order_intent is None:
                raise OrderFlowError("order_intent_not_found", "OrderIntent not found")
            if order_intent.side != command.side:
                raise OrderFlowError(
                    "order_intent_mismatch", "TradeOrder side does not match its OrderIntent"
                )

        # trading_halt (ADR 0004/docs/plans/trading-halt-mvp.md Unit C): a new or
        # same-direction/increasing order is blocked while an entry_halted-or-more
        # active halt covers this account (or bot, if known) -- closing/reducing an
        # existing opposite-direction position is always allowed (matches every row
        # in 05番's 取引停止マトリクス, which permits 決済 at entry_halted). Only the
        # two causes ADR 0004 wired up in risk_gate.py can ever set one of these
        # halts today; see that module and trading_halt.py's own docstrings for why
        # the other 8 causes never reach this check yet, and why a broader
        # workspace/system-scoped halt is not checked here either.
        existing_position = _open_position(db, account, instrument)
        is_closing_or_reducing = existing_position is not None and (
            (command.side == "sell" and existing_position.side == "long")
            or (command.side == "buy" and existing_position.side == "short")
        )
        if not is_closing_or_reducing:
            account_scope = trading_halt.HaltScope(
                workspace_id=command.workspace_id, scope_type="account", scope_id=account.id
            )
            halted = trading_halt.has_active_halt_at_or_above(
                db, account_scope, min_level="entry_halted"
            )
            if not halted and command.bot_id is not None:
                bot_scope = trading_halt.HaltScope(
                    workspace_id=command.workspace_id, scope_type="bot", scope_id=command.bot_id
                )
                halted = trading_halt.has_active_halt_at_or_above(
                    db, bot_scope, min_level="entry_halted"
                )
            if halted:
                raise OrderFlowError(
                    "trading_halted",
                    "A new or increasing entry is blocked by an active trading_halt",
                )

        now = datetime.now(UTC)
        order = TradeOrder(
            workspace_id=command.workspace_id,
            account_id=command.account_id,
            order_intent_id=command.order_intent_id,
            client_order_id=command.client_order_id,
            instrument_id=command.instrument_id,
            side=command.side,
            order_type=command.order_type,
            time_in_force=command.time_in_force,
            quantity=command.quantity,
            limit_price=command.limit_price,
            stop_price=command.stop_price,
            status="submitted",
            submitted_at=now,
        )
        db.add(order)
        db.flush()
        _audit(
            db,
            command.workspace_id,
            "trade_order.submitted",
            "trade_order",
            order.id,
            {
                "account_id": str(command.account_id),
                "instrument_id": str(command.instrument_id),
                "side": command.side,
                "order_type": command.order_type,
                "quantity": str(command.quantity),
                "order_intent_id": (
                    str(command.order_intent_id) if command.order_intent_id else None
                ),
            },
        )

        fill = _simulate_fill(db, order, instrument, exchange_code)
        _, realized_pnl = _apply_fill_to_position(
            db, account, instrument, fill, command.side, exchange_code
        )
        _record_ledger(db, account, instrument, fill, command.side, realized_pnl)
        record_account_snapshot(db, account, instrument)
        _audit(
            db,
            command.workspace_id,
            "trade_order.filled",
            "trade_order",
            order.id,
            {
                "fill_id": str(fill.id),
                "price": str(fill.price),
                "fee_amount": str(fill.fee_amount),
                "realized_pnl": str(realized_pnl),
            },
        )
        db.commit()
        db.refresh(order)
        return order


def cancel_order(db: Session, order: TradeOrder, *, reason_code: str) -> TradeOrder:
    """Cancel a TradeOrder still in a cancellable state. This implementation fills
    synchronously and immediately inside `place_order`, so in practice `order.status` is
    already a terminal state by the time any caller could reach this function -- there is no
    observable `submitted`/`partially_filled` window to race a cancel request against.
    `cancel_pending` is therefore never set here: see module docstring and
    05_アーキテクチャと移行計画.md's order state transition table for why it is reserved for a
    future async flow instead."""
    with _rollback_on_failure(db):
        if order.status not in _CANCELLABLE_STATUSES:
            raise OrderFlowError(
                "invalid_status_transition",
                f"Cannot cancel a TradeOrder in status '{order.status}'",
            )
        previous_status = order.status
        now = datetime.now(UTC)
        order.status = "cancelled"
        order.updated_at = now
        db.flush()
        _audit(
            db,
            order.workspace_id,
            "trade_order.cancelled",
            "trade_order",
            order.id,
            {"reason_code": reason_code, "previous_status": previous_status},
        )
        db.commit()
        db.refresh(order)
        return order


def _simulate_fill(
    db: Session, order: TradeOrder, instrument: Instrument, exchange_code: str
) -> Fill:
    candle = db.scalar(
        select(Candle)
        .where(Candle.instrument_id == order.instrument_id, Candle.is_final.is_(True))
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if candle is None:
        raise OrderFlowError(
            "market_price_unavailable",
            "No final candle available for this instrument; cannot simulate a fill",
        )
    fee_amount = _fee_buffer(exchange_code, candle.close, order.quantity)
    expected_slippage = _expected_slippage(db, exchange_code, order.instrument_id)
    fill_price = (
        candle.close + expected_slippage
        if order.side == "buy"
        else candle.close - expected_slippage
    )
    if fill_price <= 0:
        raise OrderFlowError("invalid_fill_price", "Simulated fill price is not positive")

    now = datetime.now(UTC)
    fill = Fill(
        order_id=order.id,
        price=fill_price,
        quantity=order.quantity,
        fee_amount=fee_amount,
        fee_asset=instrument.quote_asset,
        liquidity_role=None,
        executed_at=now,
    )
    db.add(fill)
    order.filled_quantity = order.quantity
    order.status = "filled"
    order.updated_at = now
    db.flush()
    db.refresh(fill)
    return fill


def _open_position(
    db: Session, account: TradingAccount, instrument: Instrument
) -> TradingPosition | None:
    """Standalone lookup for `place_order`'s trading_halt pre-check (ADR 0004 Unit
    C), kept separate from `_apply_fill_to_position`'s own internal position query
    rather than refactored to share one: that function's query is exercised by an
    existing, already-tested code path this task does not otherwise need to touch."""
    return db.scalar(
        select(TradingPosition).where(
            TradingPosition.account_id == account.id,
            TradingPosition.instrument_id == instrument.id,
            TradingPosition.status == "open",
        )
    )


def _exchange_code_for_account(db: Session, account: TradingAccount) -> str:
    if account.connection_id is None:
        raise OrderFlowError(
            "connection_missing",
            "Trading account has no exchange connection; cannot resolve fee/price rules",
        )
    exchange_code = db.scalar(
        select(Exchange.code)
        .join(ExchangeConnection, ExchangeConnection.exchange_id == Exchange.id)
        .where(ExchangeConnection.id == account.connection_id)
    )
    if exchange_code is None:
        raise OrderFlowError(
            "exchange_not_found", "Exchange for this account's connection could not be resolved"
        )
    return exchange_code


SLIPPAGE_COEFFICIENT_BY_EXCHANGE = {"oanda": Decimal("0.5"), "binance": Decimal("1.0")}
"""Draft, unapproved coefficients from 08_取引アルゴリズムとリスク初期値.md§5.1
(2026-09-19). Public (not `_`-prefixed) and shared with `risk_gate.py` and
`backtest_replay.py` -- /code-review finding: those two modules previously
hardcoded their own copies of these same two numbers inline, and could silently
drift out of sync with this module and each other. Binance's `1.0` entry does
not currently change `_expected_slippage`'s behavior below: no bid/ask source
exists for Binance yet (TODO(binance-spread), see module docstring), so it
still degrades to 0 there regardless of this dict's value -- absence of spread
*data*, not the coefficient, is what makes Binance 0 today. The moment a
Binance spread source exists, this coefficient applies consistently everywhere
without another find-and-replace across three files."""


def _expected_slippage(db: Session, exchange_code: str, instrument_id: UUID) -> Decimal:
    coefficient = SLIPPAGE_COEFFICIENT_BY_EXCHANGE.get(exchange_code)
    if coefficient is None:
        return Decimal(0)
    spread_row = db.get(InstrumentSpread, instrument_id)
    if spread_row is None:
        # Not yet streamed (or the stream isn't currently running for this
        # instrument) -- degrade to 0 rather than fail the fill. See module
        # docstring's "Known limitation".
        return Decimal(0)
    return (spread_row.ask - spread_row.bid) * coefficient


def _fee_buffer(exchange_code: str, price: Decimal, quantity: Decimal) -> Decimal:
    """Draft, unapproved `fee_buffer` coefficients from
    08_取引アルゴリズムとリスク初期値.md§5.1 (2026-09-19, not yet calibrated against real
    fills)."""
    if exchange_code == "oanda":
        return Decimal(0)
    if exchange_code == "binance":
        return price * quantity * Decimal("0.001")
    raise OrderFlowError(
        "unsupported_exchange", f"No fee_buffer defined for exchange '{exchange_code}'"
    )


def _apply_fill_to_position(
    db: Session,
    account: TradingAccount,
    instrument: Instrument,
    fill: Fill,
    order_side: OrderSide,
    exchange_code: str,
) -> tuple[TradingPosition, Decimal]:
    """Net position bookkeeping (one open row per account+instrument; see module
    docstring). `exchange_code == "oanda"` allows the row to flip between `long` and
    `short`; any other exchange keeps the original long-only behavior (a sell exceeding
    the held long quantity raises `short_not_supported`).

    A fill that exceeds the currently held opposite-direction quantity flips the position
    in one step -- closes the existing leg (realizing P&L on it) and opens the remainder
    in the new direction at the same fill price -- rather than requiring two separate
    orders (close, then reopen). Chosen over the two-step alternative because OANDA
    itself nets a single order this way for a netting-mode account, and Risk Gate sizing
    (which would be the natural place to *limit* how large a single flip can be) is out
    of scope for this module regardless of whether flips happen in one step or two, so
    splitting it into two steps here would not have made risk management any easier --
    only added an extra round trip. Returns (position, realized_pnl): realized_pnl is 0
    for an opening/increasing fill and the booked P&L for the closed portion of a
    reducing or flipping fill."""
    position = db.scalar(
        select(TradingPosition).where(
            TradingPosition.account_id == account.id,
            TradingPosition.instrument_id == instrument.id,
            TradingPosition.status == "open",
        )
    )
    allow_short = exchange_code == "oanda"
    now = datetime.now(UTC)
    opening_side = "long" if order_side == "buy" else "short"

    if position is None:
        if opening_side == "short" and not allow_short:
            raise OrderFlowError(
                "short_not_supported",
                "Opening a short position is not supported for this exchange "
                "(see order_flow.py module docstring)",
            )
        position = TradingPosition(
            account_id=account.id,
            instrument_id=instrument.id,
            side=opening_side,
            quantity=fill.quantity,
            average_entry_price=fill.price,
            status="open",
            opened_at=now,
            updated_at=now,
        )
        db.add(position)
        db.flush()
        db.refresh(position)
        return position, Decimal(0)

    if position.average_entry_price is None:
        raise OrderFlowError(
            "position_missing_entry_price",
            "Open position has no average_entry_price; cannot apply a fill",
        )

    same_direction = opening_side == position.side
    if same_direction:
        total_cost = position.average_entry_price * position.quantity + fill.price * fill.quantity
        position.quantity += fill.quantity
        position.average_entry_price = total_cost / position.quantity
        position.version += 1
        position.updated_at = now
        db.flush()
        db.refresh(position)
        return position, Decimal(0)

    # Opposite direction: reduce, exactly close, or flip.
    direction_sign = Decimal(1) if position.side == "long" else Decimal(-1)
    closing_quantity = min(fill.quantity, position.quantity)
    realized_pnl = (fill.price - position.average_entry_price) * closing_quantity * direction_sign
    position.realized_pnl += realized_pnl
    position.version += 1
    position.updated_at = now

    remainder = fill.quantity - position.quantity
    if remainder > 0:
        if not allow_short:
            raise OrderFlowError(
                "short_not_supported",
                "Fill quantity exceeds the held long position; opening a short position "
                "is not supported for this exchange (see order_flow.py module docstring)",
            )
        position.side = opening_side
        position.quantity = remainder
        position.average_entry_price = fill.price
        position.opened_at = now
    elif remainder == 0:
        position.quantity = Decimal(0)
        position.status = "closed"
        position.closed_at = now
    else:
        position.quantity -= fill.quantity

    db.flush()
    db.refresh(position)
    return position, realized_pnl


def _record_ledger(
    db: Session,
    account: TradingAccount,
    instrument: Instrument,
    fill: Fill,
    order_side: OrderSide,
    realized_pnl: Decimal,
) -> LedgerTransaction:
    """Only `cash`/`fee`/`realized_pnl` entry_types are used: `position`/`financing`/
    `funding`/`deposit`/`withdrawal`/`adjustment` do not apply to a simple buy/sell fill
    (see the implementation plan)."""
    transaction = LedgerTransaction(
        account_id=account.id,
        reference_type="fill",
        reference_id=fill.id,
        description=f"{order_side} {fill.quantity} {instrument.symbol} @ {fill.price}",
        occurred_at=fill.executed_at,
    )
    db.add(transaction)
    db.flush()

    notional = fill.price * fill.quantity
    cash_amount = -notional if order_side == "buy" else notional
    db.add(
        LedgerEntry(
            transaction_id=transaction.id,
            account_id=account.id,
            fill_id=fill.id,
            asset=instrument.quote_asset,
            amount=cash_amount,
            entry_type="cash",
            occurred_at=fill.executed_at,
        )
    )
    if fill.fee_amount > 0:
        db.add(
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=account.id,
                fill_id=fill.id,
                asset=fill.fee_asset or instrument.quote_asset,
                amount=-fill.fee_amount,
                entry_type="fee",
                occurred_at=fill.executed_at,
            )
        )
    if realized_pnl != 0:
        db.add(
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=account.id,
                fill_id=fill.id,
                asset=instrument.quote_asset,
                amount=realized_pnl,
                entry_type="realized_pnl",
                occurred_at=fill.executed_at,
            )
        )
    db.flush()
    return transaction


def _audit(
    db: Session,
    workspace_id: UUID,
    action: str,
    resource_type: str,
    resource_id: UUID,
    after_data: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_data=None,
            after_data=after_data,
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )
