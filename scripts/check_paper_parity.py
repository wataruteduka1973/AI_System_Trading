"""Check a paper bot against the backtest on the same bars
(docs/plans/paper-trading-live-data.md Unit 4):

1. signals -- every signal the bot recorded vs. the strategy recomputed on the
   same bars (must match exactly),
2. missed bars -- bars that closed while the bot was running but were never
   evaluated (e.g. the machine was asleep); a breakout on such a bar is an
   entry the live bot never took,
3. trades -- the bot's fills next to a backtest replay of the same period
   (quantities may differ slightly: the live Risk Gate's daily/weekly baselines
   come from real snapshots, the replay's from its own bars).

Read-only: nothing is written.

Run: python scripts/check_paper_parity.py [--bot btcusdt-4h-donchian]
"""

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.session import SessionLocal
from app.exchanges.types import timeframe_delta
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.models.strategy import BotRun, RiskProfileVersion, Signal, StrategyVersion, TradingBot
from app.models.trading import Fill, LedgerEntry, TradeOrder
from app.trading.application.backtest_provisioning import load_final_candles
from app.trading.application.backtest_replay import _HISTORY_WINDOW, run_replay
from app.trading.application.live_strategies import resolve_live_strategy
from app.trading.application.paper_parity import compare_signals, find_unevaluated_bars
from sqlalchemy import func, select


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", default="btcusdt-4h-donchian")
    args = parser.parse_args()

    with SessionLocal() as db:
        bot = db.scalar(select(TradingBot).where(TradingBot.name == args.bot))
        if bot is None:
            print(f"[NG] bot '{args.bot}' not found")
            return 1
        runs = db.scalars(
            select(BotRun).where(BotRun.bot_id == bot.id).order_by(BotRun.started_at)
        ).all()
        if not runs:
            print(f"[NG] bot '{args.bot}' has never been started")
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
                f"   [NG] {mismatch.candle_open_time:%Y-%m-%d %H:%M} recorded="
                f"{mismatch.recorded} recomputed={mismatch.recomputed}"
            )

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
            flag = "  <- an entry/exit the bot never took" if action != "hold" else ""
            print(f"   {candle.open_time:%Y-%m-%d %H:%M} would have been {action}{flag}")

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
        fills = db.execute(
            select(TradeOrder.side, Fill.price, Fill.quantity, Fill.executed_at)
            .join(Fill, Fill.order_id == TradeOrder.id)
            .where(TradeOrder.account_id == bot.account_id)
            .order_by(Fill.executed_at)
        ).all()
        print(f"3. live fills: {len(fills)} / backtest closed trades: {len(replay.trades)}")
        for side, price, quantity, executed_at in fills:
            print(f"   live     {executed_at:%Y-%m-%d %H:%M} {side:<4} {quantity} @ {price}")
        for trade in replay.trades:
            print(
                f"   backtest {trade.entry_time:%Y-%m-%d %H:%M} buy  {trade.quantity} @ "
                f"{trade.entry_price} -> {trade.exit_time:%Y-%m-%d %H:%M} {trade.exit_reason} "
                f"@ {trade.exit_price}"
            )
        if replay.ending_position is not None:
            position = replay.ending_position
            print(
                f"   backtest open: {position.side} {position.quantity} @ "
                f"{position.average_entry_price}"
            )
    return 1 if comparison.mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
