"""Check a paper bot against the backtest on the same bars
(docs/plans/paper-trading-live-data.md Unit 4):

1. signals -- every signal the bot recorded vs. the strategy recomputed on the
   same bars (must match exactly),
2. missed bars -- bars that closed while the bot was running but were never
   evaluated (e.g. the machine was asleep). Flagged only when the signal would have
   become an order given what the bot held then (entry, addition or exit),
3. trades -- the bot's fills next to a backtest replay of the same period
   (quantities may differ slightly: the live Risk Gate's daily/weekly baselines
   come from real snapshots, the replay's from its own bars).

Read-only: nothing is written. All times are UTC.

Run: python scripts/check_paper_parity.py [--bot btcusdt-4h-donchian | --all]
"""

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.session import SessionLocal
from app.exchanges.types import timeframe_delta
from app.market_data.application.public_research import RESEARCH_EXCHANGE_CODE
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import BotRun, RiskProfileVersion, Signal, StrategyVersion, TradingBot
from app.models.trading import Fill, LedgerEntry, TradeOrder
from app.trading.application.backtest_provisioning import load_final_candles
from app.trading.application.backtest_replay import _HISTORY_WINDOW, run_replay
from app.trading.application.live_strategies import resolve_live_strategy
from app.trading.application.paper_parity import (
    compare_signals,
    find_unevaluated_bars,
    held_quantity_at,
    missed_bar_effect,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _utc(time: datetime) -> str:
    # The database returns times in the session's time zone (e.g. JST); every time this
    # script prints is UTC, which is how bars are aligned (2026-10-05: they printed as JST).
    return f"{time.astimezone(UTC):%Y-%m-%d %H:%M}"


def _check_bot(db: Session, bot: TradingBot) -> int:
    """Prints the three checks for one bot; returns 1 on a signal mismatch or if
    the bot has never run."""
    runs = db.scalars(
        select(BotRun).where(BotRun.bot_id == bot.id).order_by(BotRun.started_at)
    ).all()
    if not runs:
        print(f"[NG] bot '{bot.name}' has never been started")
        return 1
    instrument = db.get(Instrument, bot.instrument_id)
    strategy_version = db.get(StrategyVersion, bot.strategy_version_id)
    risk_profile_version = db.get(RiskProfileVersion, bot.risk_profile_version_id)
    assert instrument is not None and strategy_version is not None
    assert risk_profile_version is not None
    strategy = resolve_live_strategy(strategy_version.definition)

    first_start = runs[0].started_at.astimezone(UTC)
    warmup_start = first_start - timeframe_delta(bot.timeframe) * _HISTORY_WINDOW
    candles = load_final_candles(
        db, instrument.id, bot.timeframe, warmup_start, datetime.now(UTC) + timedelta(days=1)
    )
    recorded = {
        signal.candle_id: signal.action
        for signal in db.scalars(
            select(Signal).where(Signal.bot_run_id.in_([run.id for run in runs]))
        ).all()
    }

    print(
        f"=== {bot.name} ({instrument.symbol} {bot.timeframe}, {strategy.kind}, "
        f"exit_policy={strategy.exit_policy}) since {first_start:%Y-%m-%d %H:%M} UTC ==="
    )

    comparison = compare_signals(
        candles, recorded, strategy.generate, history_window=_HISTORY_WINDOW
    )
    print(f"1. signals: {comparison.matched} match, {len(comparison.mismatches)} mismatch")
    for mismatch in comparison.mismatches:
        print(
            f"   [NG] {_utc(mismatch.candle_open_time)} recorded="
            f"{mismatch.recorded} recomputed={mismatch.recomputed}"
        )

    fills = db.execute(
        select(TradeOrder.side, Fill.price, Fill.quantity, Fill.executed_at)
        .join(Fill, Fill.order_id == TradeOrder.id)
        .where(TradeOrder.account_id == bot.account_id)
        .order_by(Fill.executed_at)
    ).all()
    held_by_fill = [(side, quantity, executed_at) for side, _price, quantity, executed_at in fills]

    missed: list[Candle] = []
    for run in runs:
        ended = run.stopped_at or datetime.now(UTC)
        in_run = [c for c in candles if c.close_time <= ended]
        missed += find_unevaluated_bars(in_run, set(recorded), running_since=run.started_at)
    print(f"2. bars closed while running but never evaluated: {len(missed)}")
    for candle in missed:
        action = strategy.generate(
            [c for c in candles if c.open_time <= candle.open_time][-_HISTORY_WINDOW:]
        )
        held = held_quantity_at(held_by_fill, candle.close_time)
        effect = missed_bar_effect(action, held)
        if effect is not None:
            note = f"  <- a missed {effect} (held {held})"
        elif action == "sell":
            note = "  (nothing held; spot cannot short, so no order)"
        else:
            note = ""
        print(f"   {_utc(candle.open_time)} would have been {action}{note}")

    initial_equity = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == bot.account_id, LedgerEntry.entry_type == "deposit"
        )
    )
    warmup = sum(1 for c in candles if c.close_time <= first_start)
    replay = run_replay(
        candles,
        instrument=instrument,
        timeframe=bot.timeframe,
        exchange_code="binance",
        rules=risk_profile_version.rules,
        initial_equity=Decimal(initial_equity or 0),
        signal_generator=strategy.generate,
        warmup_bars=warmup,
        exit_policy=strategy.exit_policy,
    )
    print(f"3. live fills: {len(fills)} / backtest closed trades: {len(replay.trades)}")
    for side, price, quantity, executed_at in fills:
        print(f"   live     {_utc(executed_at)} {side:<4} {quantity} @ {price}")
    for trade in replay.trades:
        print(
            f"   backtest {_utc(trade.entry_time)} buy  {trade.quantity} @ "
            f"{trade.entry_price} -> {_utc(trade.exit_time)} {trade.exit_reason} "
            f"@ {trade.exit_price}"
        )
    if replay.ending_position is not None:
        position = replay.ending_position
        print(
            f"   backtest open: {position.side} {position.quantity} @ "
            f"{position.average_entry_price}"
        )
    return 1 if comparison.mismatches else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--bot", default="btcusdt-4h-donchian")
    target.add_argument(
        "--all", action="store_true", help="every bot that trades on public production prices"
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.all:
            bots = db.scalars(
                select(TradingBot)
                .join(Instrument, Instrument.id == TradingBot.instrument_id)
                .join(Exchange, Exchange.id == Instrument.exchange_id)
                .where(Exchange.code == RESEARCH_EXCHANGE_CODE)
                .order_by(TradingBot.name)
            ).all()
        else:
            bot = db.scalar(select(TradingBot).where(TradingBot.name == args.bot))
            bots = [bot] if bot is not None else []
        if not bots:
            print("[NG] no matching bot")
            return 1
        results = []
        for bot in bots:
            results.append(_check_bot(db, bot))
            print()
    failed = sum(results)
    print(f"=> {len(results) - failed}/{len(results)} bot(s) consistent with the backtest")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
