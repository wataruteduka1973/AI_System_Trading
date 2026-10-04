"""Stop-monitoring timeframe comparison (2026-10-04, per user request): decide on
4h bars (the validated timeframe), but check stops on finer bars -- does finer
monitoring change the risk taken, and can a trailing stop on those bars add
profit?

Fixed before looking at any result:
- monitoring: the 4h bars themselves, 1h, 15m, 5m (Binance has no 10m bars);
- exits: `stop_loss` (fixed stop at 2xATR) and `trailing_stop` (the same 2xATR
  distance, ratcheting up behind the highest high since entry);
- the same rolling walk-forward (90-day train / 30-day test) as the other
  research scripts.

With a fixed stop, finer monitoring cannot move the stop level; what it shows is
how much worse a stop really fills when the price falls through it inside a 4h
bar (a finer bar that opens past the stop fills at its own open). With a
trailing stop, finer monitoring also changes how closely the stop follows the
price.

Research only: nothing is persisted. The finer series must be fetched first:
`python scripts/fetch_binance_public_history.py --symbol BTCUSDT --days 3400
--timeframes 1h,15m,5m`.

Run: python scripts/research/compare_stop_monitoring.py [--symbol BTCUSDT]
"""

import argparse
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

from app.db.session import SessionLocal
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.application.public_research import (
    RESEARCH_EXCHANGE_CODE,
    find_public_research_instrument,
)
from app.models.market_data import Candle
from app.trading.application import backtest_robustness as rb
from app.trading.application.backtest_provisioning import (
    _exchange_code_for_instrument,
    load_final_candles,
)
from app.trading.application.backtest_replay import ExitPolicy, MonitorBar, TradeRecord
from app.trading.application.backtest_walk_forward import run_rolling_walk_forward
from app.trading.application.research_report import (
    INITIAL_EQUITY,
    strategy_windows,
    tested_years,
)
from app.trading.application.research_strategies import RESEARCH_STRATEGIES
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES
from sqlalchemy import select
from sqlalchemy.orm import Session

DECISION_TIMEFRAME = "4h"
MONITOR_TIMEFRAMES = ("1h", "15m", "5m")
EXIT_POLICIES: tuple[ExitPolicy, ...] = ("stop_loss", "trailing_stop")
STRATEGY = "donchian_55_20"
TRAIN_DAYS = 90
TEST_DAYS = 30


def load_monitor_bars(
    db: Session, instrument_id: object, timeframe: str
) -> dict[datetime, list[MonitorBar]]:
    """Finer bars grouped under the decision bar (4h, UTC-aligned) they fall in."""
    decision_seconds = TIMEFRAME_SECONDS[DECISION_TIMEFRAME]
    rows = db.execute(
        select(Candle.open_time, Candle.close_time, Candle.open, Candle.high, Candle.low)
        .where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == timeframe,
            Candle.is_final.is_(True),
        )
        .order_by(Candle.open_time)
    ).all()
    grouped: dict[datetime, list[MonitorBar]] = defaultdict(list)
    for open_time, close_time, open_, high, low in rows:
        bucket = int(open_time.timestamp()) // decision_seconds * decision_seconds
        grouped[datetime.fromtimestamp(bucket, UTC)].append(
            MonitorBar(open_time=open_time, close_time=close_time, open=open_, high=high, low=low)
        )
    return grouped


def stop_out_line(trades: Sequence[TradeRecord]) -> str:
    """Losses of the trades that ended at a stop, as a share of initial equity."""
    stops = [t for t in trades if t.exit_reason == "stop_loss"]
    if not stops:
        return "stop-outs=0"
    losses = [float((t.realized_pnl - t.fees) / INITIAL_EQUITY) for t in stops]
    return (
        f"stop-outs={len(stops)} avg={sum(losses) / len(losses):+.3%} "
        f"worst={min(losses):+.3%} of equity"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    with SessionLocal() as db:
        instrument = find_public_research_instrument(db, args.symbol)
        if instrument is None:
            print(f"[NG] no {RESEARCH_EXCHANGE_CODE} instrument for {args.symbol}")
            return 1
        exchange_code = _exchange_code_for_instrument(db, instrument)
        candles = load_final_candles(
            db,
            instrument.id,
            DECISION_TIMEFRAME,
            datetime(2000, 1, 1, tzinfo=UTC),
            datetime.now(UTC),
        )
        monitors: dict[str, dict[datetime, list[MonitorBar]] | None] = {"4h": None}
        for timeframe in MONITOR_TIMEFRAMES:
            print(f"[..] loading {timeframe} bars", flush=True)
            monitors[timeframe] = load_monitor_bars(db, instrument.id, timeframe)

    bars_per_day = 86_400 // TIMEFRAME_SECONDS[DECISION_TIMEFRAME]
    years = 0.0
    lines: list[str] = []
    for policy in EXIT_POLICIES:
        for monitor_name, monitor in monitors.items():
            print(f"[..] {policy} monitored on {monitor_name}", flush=True)
            results = run_rolling_walk_forward(
                candles,
                instrument=instrument,
                timeframe=DECISION_TIMEFRAME,
                exchange_code=exchange_code,
                rules=CONSERVATIVE_V1_RULES,
                initial_equity=INITIAL_EQUITY,
                train_bars=TRAIN_DAYS * bars_per_day,
                test_bars=TEST_DAYS * bars_per_day,
                signal_generator=RESEARCH_STRATEGIES[STRATEGY],
                exit_policy=policy,
                stop_monitor=monitor,
            )
            tests = [r.test_result for r in results]
            span, years = tested_years(candles, [r.fold for r in results])
            summary = rb.summarize_windows(strategy_windows(tests), years=years)
            label = f"{policy} / monitor {monitor_name}"
            lines.append(rb.format_summary(label, summary))
            lines.append("    " + stop_out_line([t for r in tests for t in r.trades]))

    print(f"\n=== {args.symbol}: decide on {DECISION_TIMEFRAME}, {STRATEGY}, {years:.1f}y ===")
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
