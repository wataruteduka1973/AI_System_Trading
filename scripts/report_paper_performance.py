"""Paper results next to what the backtest expects, per strategy version
(docs/plans/paper-trading-live-data.md Unit 6). For each strategy version, its bots'
accounts are combined into one portfolio and shown as:

1. live -- equity at every bar close rebuilt from the ledger and fills,
2. same-period backtest -- every bot replayed on the same bars with the same money
   (any difference is execution drift: unevaluated bars, lock timing, ...),
3. expected range -- the 5% / 50% / 95% points of the same measures over many
   historical windows as long as the live period, each replayed from flat with no
   locks, and where the live value falls among them,
4. open positions now, live and backtest, and data health -- bars that closed while
   running but were never evaluated, and orders the Risk Gate refused, by rule.

Everything except the open positions is measured up to the newest bar close, the
last point at which live equity can be marked. Returns are equity-based, so they
already include every fee; there is no separate fee column, because the replay only
books a trade's fees when it closes and the two would not compare while a position
is open.

Warnings (max drawdown beyond the 95% point, return below the 5% point) are only
printed: no bot is stopped. Run it monthly, when the locks are reviewed.

Read-only: nothing is written. `--stride-days` sets how far apart the historical
windows start.

Run: python scripts/report_paper_performance.py [--stride-days 7]
"""

import argparse
import bisect
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.db.session import SessionLocal
from app.exchanges.types import timeframe_delta
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import (
    BotRun,
    RiskDecision,
    RiskProfileVersion,
    Signal,
    Strategy,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import Fill, LedgerEntry, TradeOrder, TradingPosition
from app.trading.application import paper_performance as pp
from app.trading.application.backtest_provisioning import load_final_candles
from app.trading.application.backtest_replay import _HISTORY_WINDOW, ReplayResult, run_replay
from app.trading.application.live_strategies import (
    LiveStrategy,
    UnresolvableStrategyError,
    resolve_live_strategy,
)
from app.trading.application.paper_parity import find_unevaluated_bars
from sqlalchemy import select
from sqlalchemy.orm import Session

_CASH_ENTRY_TYPES = ("cash", "fee", "deposit", "withdrawal")
_EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class BotData:
    bot: TradingBot
    instrument: Instrument
    strategy: LiveStrategy
    rules: dict
    started_at: datetime
    deposits: Decimal
    history: list[Candle]
    """Every stored final bar of the bot's instrument and timeframe, ascending."""
    live_curve: pp.EquityCurve
    live_pnls: list[tuple[datetime, Decimal]]
    """(occurred_at, amount) of the account's `realized_pnl` ledger entries."""
    open_position: tuple[Decimal, Decimal] | None
    """(quantity, average entry price) of the account's open position, if any."""
    unevaluated_bars: int
    refused_by_rule: Counter[str]


def _load_bot(db: Session, bot: TradingBot, now: datetime) -> BotData | None:
    runs = db.scalars(
        select(BotRun).where(BotRun.bot_id == bot.id).order_by(BotRun.started_at)
    ).all()
    strategy_version = db.get(StrategyVersion, bot.strategy_version_id)
    if not runs or strategy_version is None:
        return None
    try:
        strategy = resolve_live_strategy(strategy_version.definition)
    except UnresolvableStrategyError:
        return None  # e.g. the retired SMA skeleton: nothing comparable to report
    instrument = db.get(Instrument, bot.instrument_id)
    risk_profile_version = db.get(RiskProfileVersion, bot.risk_profile_version_id)
    assert instrument is not None and risk_profile_version is not None
    started_at = runs[0].started_at.astimezone(UTC)
    run_ids = [run.id for run in runs]

    history = load_final_candles(db, instrument.id, bot.timeframe, _EARLIEST, now)
    entries = db.execute(
        select(LedgerEntry.entry_type, LedgerEntry.amount, LedgerEntry.occurred_at).where(
            LedgerEntry.account_id == bot.account_id
        )
    ).all()
    fills = db.execute(
        select(TradeOrder.side, Fill.quantity, Fill.executed_at)
        .join(Fill, Fill.order_id == TradeOrder.id)
        .where(TradeOrder.account_id == bot.account_id)
    ).all()
    position = db.scalar(
        select(TradingPosition).where(
            TradingPosition.account_id == bot.account_id, TradingPosition.status == "open"
        )
    )

    recorded = set(db.scalars(select(Signal.candle_id).where(Signal.bot_run_id.in_(run_ids))).all())
    unevaluated = 0
    for run in runs:
        ended = run.stopped_at or now
        in_run = [c for c in history if c.close_time <= ended]
        unevaluated += len(find_unevaluated_bars(in_run, recorded, running_since=run.started_at))

    refused: Counter[str] = Counter()
    for rule_results in db.scalars(
        select(RiskDecision.rule_results)
        .join(Signal, Signal.id == RiskDecision.signal_id)
        .where(Signal.bot_run_id.in_(run_ids), RiskDecision.outcome == "deny")
    ).all():
        refused.update(
            rule
            for rule, result in rule_results.items()
            if isinstance(result, dict) and result.get("passed") is False
        )

    return BotData(
        bot=bot,
        instrument=instrument,
        strategy=strategy,
        rules=risk_profile_version.rules,
        started_at=started_at,
        deposits=sum(
            (amount for entry_type, amount, _ in entries if entry_type == "deposit"), Decimal(0)
        ),
        history=history,
        live_curve=pp.live_equity_curve(
            [(c.close_time, c.close) for c in history if c.close_time > started_at],
            [
                pp.CashMovement(occurred_at, amount)
                for entry_type, amount, occurred_at in entries
                if entry_type in _CASH_ENTRY_TYPES
            ],
            [(side, quantity, executed_at) for side, quantity, executed_at in fills],
        ),
        live_pnls=[
            (occurred_at, amount)
            for entry_type, amount, occurred_at in entries
            if entry_type == "realized_pnl"
        ],
        open_position=(
            (position.quantity, position.average_entry_price)
            if position is not None and position.average_entry_price is not None
            else None
        ),
        unevaluated_bars=unevaluated,
        refused_by_rule=refused,
    )


def _replay(data: BotData, start: datetime, end: datetime) -> ReplayResult | None:
    """The bot's strategy on the bars that close in (start, end], from flat with the
    bot's own money, with the `_HISTORY_WINDOW` bars before as history -- the same
    call `check_paper_parity.py` makes. None if the history does not reach back far
    enough to warm up, or no bar closes in the window."""
    close_times = [c.close_time for c in data.history]
    first = bisect.bisect_right(close_times, start)
    last = bisect.bisect_right(close_times, end)
    if first < _HISTORY_WINDOW or first >= last:
        return None
    return run_replay(
        data.history[first - _HISTORY_WINDOW : last],
        instrument=data.instrument,
        timeframe=data.bot.timeframe,
        exchange_code="binance",
        rules=data.rules,
        initial_equity=data.deposits,
        signal_generator=data.strategy.generate,
        warmup_bars=_HISTORY_WINDOW,
        exit_policy=data.strategy.exit_policy,
    )


def _row(label: str, summary: pp.CurveSummary, counts: pp.TradeCounts) -> str:
    return (
        f"  {label:<22} {summary.return_pct:>+8.2%} {summary.max_drawdown_pct:>8.2%} "
        f"{counts.closes:>7} {counts.wins:>5}"
    )


def _position(symbol: str, quantity: Decimal, price: Decimal) -> str:
    return f"{symbol} {quantity.normalize()} @ {price.normalize()}"


def _report_group(name: str, bots: list[BotData], now: datetime, stride: timedelta) -> None:
    start = min(data.started_at for data in bots)
    deposits = sum((data.deposits for data in bots), Decimal(0))
    timeframe = bots[0].bot.timeframe
    live_curve = pp.combine_curves([data.live_curve for data in bots])
    cutoff = live_curve[-1][0].astimezone(UTC) if live_curve else start
    length = cutoff - start
    print(
        f"=== {name}: {len(bots)} bot(s), {timeframe}, since {start:%Y-%m-%d %H:%M} UTC, "
        f"measured to the bar close at {cutoff:%Y-%m-%d %H:%M} UTC "
        f"({length.total_seconds() / 86400:.1f} days), starting equity {deposits:,.0f} ==="
    )
    print(f"  {'':<22} {'return':>8} {'max DD':>8} {'closes':>7} {'wins':>5}")

    live = pp.summarize(live_curve, deposits)
    live_counts = pp.count_trades(
        [amount for data in bots for occurred_at, amount in data.live_pnls if occurred_at <= cutoff]
    )
    print(_row("live", live, live_counts))

    same_period = [_replay(data, data.started_at, cutoff) for data in bots]
    replays = [result for result in same_period if result is not None]
    if replays and len(replays) == len(bots):
        print(
            _row(
                "same-period backtest",
                pp.summarize(pp.combine_curves([r.equity_curve for r in replays]), deposits),
                pp.count_trades([t.realized_pnl for r in replays for t in r.trades]),
            )
        )

    if length < timeframe_delta(timeframe):
        print("  expected range: not yet (no bar has closed since the start)")
    else:
        history_start = max(data.history[_HISTORY_WINDOW].close_time for data in bots).astimezone(
            UTC
        )
        returns: list[float] = []
        drawdowns: list[float] = []
        for window_start in pp.window_starts(history_start, start, length=length, stride=stride):
            window = [_replay(data, window_start, window_start + length) for data in bots]
            if any(result is None for result in window):
                continue
            summary = pp.summarize(
                pp.combine_curves([r.equity_curve for r in window if r is not None]), deposits
            )
            returns.append(summary.return_pct)
            drawdowns.append(summary.max_drawdown_pct)
        if not returns:
            print("  expected range: no historical window could be replayed")
        else:
            return_range = pp.expected_range(returns)
            drawdown_range = pp.expected_range(drawdowns)
            print(
                f"  expected range over {return_range.samples} historical windows of the same "
                f"length (starting every {stride.days} days since {history_start:%Y-%m-%d}):"
            )
            print(
                f"    return 5/50/95%: {return_range.low:+.2%} / {return_range.median:+.2%} / "
                f"{return_range.high:+.2%}   live at "
                f"{pp.share_at_or_below(returns, live.return_pct):.0%}"
            )
            print(
                f"    max DD 5/50/95%: {drawdown_range.low:.2%} / {drawdown_range.median:.2%} / "
                f"{drawdown_range.high:.2%}   live at "
                f"{pp.share_at_or_below(drawdowns, live.max_drawdown_pct):.0%}"
            )
            for warning in pp.range_warnings(live, return_range, drawdown_range):
                print(f"  [WARN] {warning}")

    live_open = [_position(d.instrument.symbol, *d.open_position) for d in bots if d.open_position]
    now_replays = [(d, _replay(d, d.started_at, now)) for d in bots]
    backtest_open = [
        _position(
            d.instrument.symbol, r.ending_position.quantity, r.ending_position.average_entry_price
        )
        for d, r in now_replays
        if r is not None and r.ending_position is not None
    ]
    print(f"  open now: live {', '.join(live_open) or 'none'}")
    print(f"            backtest {', '.join(backtest_open) or 'none'}")

    unevaluated = {d.bot.name: d.unevaluated_bars for d in bots if d.unevaluated_bars}
    refused = sum((d.refused_by_rule for d in bots), Counter[str]())
    print(
        f"  data health: unevaluated bars {sum(unevaluated.values())}"
        + (f" ({', '.join(f'{k} {v}' for k, v in unevaluated.items())})" if unevaluated else "")
        + ", refused by the Risk Gate: "
        + (", ".join(f"{rule} {n}" for rule, n in refused.most_common()) or "none")
    )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stride-days", type=int, default=7)
    args = parser.parse_args()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        groups: dict[UUID, list[BotData]] = {}
        for bot in db.scalars(select(TradingBot).order_by(TradingBot.created_at)).all():
            data = _load_bot(db, bot, now)
            if data is not None:
                groups.setdefault(bot.strategy_version_id, []).append(data)
        if not groups:
            print("No paper bot has run a resolvable strategy yet.")
            return 0
        for strategy_version_id, bots in groups.items():
            version = db.get(StrategyVersion, strategy_version_id)
            strategy = db.get(Strategy, version.strategy_id) if version else None
            name = f"{strategy.name} v{version.version}" if strategy and version else "?"
            _report_group(name, bots, now, timedelta(days=args.stride_days))
        db.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
