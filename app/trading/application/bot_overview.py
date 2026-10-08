"""What each bot and paper account stands at right now, for the Bot management screen
(docs/plans/bot-overview.md): where the money is, the open position, and what it has made.

Everything is read off the records the order flow already keeps (`ledger_entry`,
`trading_position`, `candle`) -- nothing is stored for this. The equity of a bot's account is
`account_valuation.value_account`, the same figure the Risk Gate sizes positions with, so the screen
cannot disagree with what the bot is using. Today one account backs one bot on one instrument (the
valuation is per instrument); an account with no bot has no instrument to value, so it is shown by
its ledger balances only.

`return_pct` is `(equity - deposits) / deposits` in the instrument's quote asset: what the account
is worth against what was put in, after fees. `realized_pnl` is the gross profit of the closed
trades (the fees are separate in `fees_paid`).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import TradingBot
from app.models.trading import LedgerEntry, TradingAccount, TradingPosition
from app.trading.application.account_valuation import value_account

_BALANCE_ENTRY_TYPES = ("cash", "fee", "deposit", "withdrawal")


@dataclass(frozen=True)
class PositionOverview:
    side: str
    quantity: Decimal
    average_entry_price: Decimal | None
    mark_price: Decimal | None
    """The latest final candle's close; `None` when there is no candle."""
    unrealized_pnl: Decimal
    stop_price: Decimal | None
    opened_at: datetime | None


@dataclass(frozen=True)
class BotOverview:
    bot: TradingBot
    instrument: Instrument
    exchange_code: str
    quote_asset: str
    deposits: Decimal
    cash: Decimal
    equity: Decimal
    return_pct: Decimal | None
    """`None` when nothing was deposited."""
    realized_pnl: Decimal
    fees_paid: Decimal
    closed_trades: int
    position: PositionOverview | None


@dataclass(frozen=True)
class AccountOverview:
    account: TradingAccount
    bot_names: list[str] = field(default_factory=list)
    balances: dict[str, Decimal] = field(default_factory=dict)
    """Cash balance per asset: deposits, trade proceeds and fees."""


def _ledger_totals(db: Session, account_ids: list[UUID]) -> dict[tuple[UUID, str, str], Decimal]:
    rows = db.execute(
        select(
            LedgerEntry.account_id,
            LedgerEntry.asset,
            LedgerEntry.entry_type,
            func.coalesce(func.sum(LedgerEntry.amount), 0),
        )
        .where(
            LedgerEntry.account_id.in_(account_ids),
            LedgerEntry.entry_type.in_(_BALANCE_ENTRY_TYPES),
        )
        .group_by(LedgerEntry.account_id, LedgerEntry.asset, LedgerEntry.entry_type)
    ).all()
    return {(row[0], row[1], row[2]): Decimal(row[3]) for row in rows}


def _mark_price(db: Session, instrument_id: UUID) -> Decimal | None:
    return db.scalar(
        select(Candle.close)
        .where(Candle.instrument_id == instrument_id, Candle.is_final.is_(True))
        .order_by(Candle.open_time.desc())
        .limit(1)
    )


def build_overview(
    db: Session, workspace_id: UUID
) -> tuple[list[BotOverview], list[AccountOverview]]:
    accounts = list(
        db.scalars(
            select(TradingAccount)
            .where(TradingAccount.workspace_id == workspace_id)
            .order_by(TradingAccount.created_at)
        ).all()
    )
    account_ids = [account.id for account in accounts]
    bot_rows = db.execute(
        select(TradingBot, Instrument, Exchange.code)
        .join(Instrument, Instrument.id == TradingBot.instrument_id)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
        .where(TradingBot.workspace_id == workspace_id)
        .order_by(TradingBot.created_at)
    ).all()
    if not account_ids:
        return [], []

    totals = _ledger_totals(db, account_ids)
    positions = db.scalars(
        select(TradingPosition).where(TradingPosition.account_id.in_(account_ids))
    ).all()
    positions_by_pair: dict[tuple[UUID, UUID], list[TradingPosition]] = defaultdict(list)
    for position in positions:
        positions_by_pair[(position.account_id, position.instrument_id)].append(position)

    bots: list[BotOverview] = []
    for bot, instrument, exchange_code in bot_rows:
        quote = instrument.quote_asset
        account = next(a for a in accounts if a.id == bot.account_id)
        valuation = value_account(db, account, instrument)
        deposits = totals.get((account.id, quote, "deposit"), Decimal(0)) + totals.get(
            (account.id, quote, "withdrawal"), Decimal(0)
        )
        rows = positions_by_pair.get((account.id, instrument.id), [])
        open_position = next((p for p in rows if p.status == "open"), None)
        bots.append(
            BotOverview(
                bot=bot,
                instrument=instrument,
                exchange_code=exchange_code,
                quote_asset=quote,
                deposits=deposits,
                cash=valuation.cash,
                equity=valuation.equity,
                return_pct=(
                    (valuation.equity - deposits) / deposits * 100 if deposits > 0 else None
                ),
                realized_pnl=sum((p.realized_pnl for p in rows), Decimal(0)),
                fees_paid=-totals.get((account.id, quote, "fee"), Decimal(0)),
                closed_trades=sum(1 for p in rows if p.status == "closed"),
                position=(
                    PositionOverview(
                        side=open_position.side,
                        quantity=open_position.quantity,
                        average_entry_price=open_position.average_entry_price,
                        mark_price=_mark_price(db, instrument.id),
                        unrealized_pnl=valuation.unrealized_pnl,
                        stop_price=open_position.stop_price,
                        opened_at=open_position.opened_at,
                    )
                    if open_position is not None
                    else None
                ),
            )
        )

    names_by_account: dict[UUID, list[str]] = defaultdict(list)
    for bot, _, _ in bot_rows:
        names_by_account[bot.account_id].append(bot.name)
    account_overviews = []
    for account in accounts:
        balances: dict[str, Decimal] = defaultdict(Decimal)
        for (account_id, asset, _), amount in totals.items():
            if account_id == account.id:
                balances[asset] += amount
        account_overviews.append(
            AccountOverview(
                account=account,
                bot_names=names_by_account.get(account.id, []),
                balances=dict(balances),
            )
        )
    return bots, account_overviews
