"""Rolling walk-forward report for the research instrument's real history
(docs/plans/rolling-walk-forward.md): every strategy x timeframe is evaluated
on fixed-size train/test folds sliding across the whole stored series, and
each fold's test result is printed next to the market's own buy-and-hold
return over the same window -- so "the strategy lost" can be read against
"the market fell" rather than in isolation.

Research only: nothing is persisted. `net` counts closed trades only (see
`compute_metrics`); `open` marks a window that ended with a position still
held, whose P&L `net` therefore leaves out. Mark-to-market equity is
deliberately not shown: while a position is held, `run_replay`'s equity
(cash + unrealized P&L) omits the position's cost basis, which the cash
balance has already paid out (tracked separately as a known issue).

Run: python scripts/run_walk_forward_report.py [--symbol BTCJPY]
     [--timeframes 15m,1h,4h] [--train-days 90] [--test-days 30]
"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.db.session import SessionLocal
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.application.public_research import RESEARCH_EXCHANGE_CODE
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application.backtest_metrics import ReplayMetrics
from app.trading.application.backtest_provisioning import (
    _exchange_code_for_instrument,
    load_final_candles,
)
from app.trading.application.backtest_replay import BacktestSignalGenerator
from app.trading.application.backtest_walk_forward import (
    RollingFoldResult,
    run_rolling_walk_forward,
)
from app.trading.application.dummy_signal import generate_dummy_signal
from app.trading.application.ema_trend_signal import generate_ema_trend_signal
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES
from sqlalchemy import select

STRATEGIES: dict[str, BacktestSignalGenerator] = {
    "dummy_sma5": generate_dummy_signal,
    "ema_trend": generate_ema_trend_signal,
}
INITIAL_EQUITY = Decimal(1_000_000)


def _bars_per_day(timeframe: str) -> int:
    return 86_400 // TIMEFRAME_SECONDS[timeframe]


def _buy_and_hold_return(candles: Sequence[Candle]) -> Decimal:
    return candles[-1].close / candles[0].close - 1


def _metrics_cells(metrics: ReplayMetrics) -> str:
    pf = "-" if metrics.profit_factor is None else f"{metrics.profit_factor:.2f}"
    return (
        f"trades={metrics.trade_count:>3} win={float(metrics.win_rate):>6.1%} "
        f"net={metrics.net_pnl:>8.0f} pf={pf:>5}"
    )


def _print_folds(candles: Sequence[Candle], results: list[RollingFoldResult]) -> None:
    total_net = Decimal(0)
    positive_folds = 0
    for r in results:
        test_window = candles[r.fold.train_end : r.fold.test_end]
        total_net += r.test_metrics.net_pnl
        positive_folds += r.test_metrics.net_pnl > 0
        open_at_end = "open" if r.test_result.ending_position is not None else "    "
        print(
            f"  fold {r.fold.index:>2} test {test_window[0].open_time:%Y-%m-%d}"
            f"..{test_window[-1].close_time:%Y-%m-%d} "
            f"market={float(_buy_and_hold_return(test_window)):>+7.1%} | "
            f"{_metrics_cells(r.test_metrics)} {open_at_end} | "
            f"train net={r.train_metrics.net_pnl:>8.0f}"
        )
    print(
        f"  => test folds with net>0: {positive_folds}/{len(results)}, "
        f"total test net={total_net:.0f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCJPY")
    parser.add_argument("--timeframes", default="15m,1h,4h")
    parser.add_argument("--train-days", type=int, default=90)
    parser.add_argument("--test-days", type=int, default=30)
    args = parser.parse_args()

    with SessionLocal() as db:
        instrument = db.scalar(
            select(Instrument)
            .join(Exchange, Exchange.id == Instrument.exchange_id)
            .where(Exchange.code == RESEARCH_EXCHANGE_CODE, Instrument.symbol == args.symbol)
        )
        if instrument is None:
            print(
                f"[NG] no {RESEARCH_EXCHANGE_CODE} instrument for {args.symbol} -- run "
                f"scripts/fetch_binance_public_history.py --symbol {args.symbol} first"
            )
            return 1
        exchange_code = _exchange_code_for_instrument(db, instrument)

        for timeframe in args.timeframes.split(","):
            candles = load_final_candles(
                db, instrument.id, timeframe, datetime(2000, 1, 1, tzinfo=UTC), datetime.now(UTC)
            )
            bars_per_day = _bars_per_day(timeframe)
            print(
                f"\n=== {args.symbol} {timeframe}: {len(candles)} candles, "
                f"train={args.train_days}d test={args.test_days}d ==="
            )
            for name, signal_generator in STRATEGIES.items():
                print(f"-- {name} --")
                results = run_rolling_walk_forward(
                    candles,
                    instrument=instrument,
                    timeframe=timeframe,
                    exchange_code=exchange_code,
                    rules=CONSERVATIVE_V1_RULES,
                    initial_equity=INITIAL_EQUITY,
                    train_bars=args.train_days * bars_per_day,
                    test_bars=args.test_days * bars_per_day,
                    signal_generator=signal_generator,
                )
                _print_folds(candles, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
