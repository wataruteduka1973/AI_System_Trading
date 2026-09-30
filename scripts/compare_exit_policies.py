"""Compare exit policies (`signal` / `stop_loss` / `stop_and_target`) on the
two questions asked on 2026-09-30, with not losing money as the priority:

1. Which is more likely to keep making money? -- compounded return, how it
   splits between the first and second half of the history and the most
   recent 24 windows, and how much of it hinges on the 5 best windows.
2. Which copes better with sudden crashes? -- maximum drawdown of the chained
   curve, longest time underwater, worst day, worst 30-day window, worst
   single trade, and the result through each historical crash.

Each strategy x policy runs the same rolling walk-forward as
`run_walk_forward_report.py`; its test windows are compounded into one curve
(see `app/trading/application/backtest_robustness.py` for why). The
benchmark `hold_10pct` holds 10% of equity -- one order's size under the
Risk Gate -- through every test window.

Research only: nothing is persisted.

Run: python scripts/compare_exit_policies.py [--symbol BTCUSDT] [--timeframe 4h]
     [--strategies donchian_55_20,donchian_20_10,ema_trend]
"""

import argparse
from datetime import UTC, datetime
from typing import get_args

from app.db.session import SessionLocal
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.application.public_research import (
    RESEARCH_EXCHANGE_CODE,
    find_public_research_instrument,
)
from app.trading.application import backtest_robustness as rb
from app.trading.application.backtest_provisioning import (
    _exchange_code_for_instrument,
    load_final_candles,
)
from app.trading.application.backtest_replay import ExitPolicy, ReplayResult
from app.trading.application.backtest_walk_forward import (
    RollingFold,
    run_rolling_walk_forward,
)
from app.trading.application.research_report import (
    INITIAL_EQUITY,
    benchmark_windows,
    crash_header,
    crash_line,
    market_crash_line,
    strategy_windows,
    tested_years,
    trade_line,
)
from app.trading.application.research_strategies import RESEARCH_STRATEGIES
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES

TRAIN_DAYS = 90
TEST_DAYS = 30


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--strategies", default="donchian_55_20,donchian_20_10,ema_trend")
    args = parser.parse_args()
    names = args.strategies.split(",")
    unknown = set(names) - set(RESEARCH_STRATEGIES)
    if unknown:
        parser.error(f"unknown strategies: {', '.join(sorted(unknown))}")

    with SessionLocal() as db:
        instrument = find_public_research_instrument(db, args.symbol)
        if instrument is None:
            print(f"[NG] no {RESEARCH_EXCHANGE_CODE} instrument for {args.symbol}")
            return 1
        exchange_code = _exchange_code_for_instrument(db, instrument)
        candles = load_final_candles(
            db, instrument.id, args.timeframe, datetime(2000, 1, 1, tzinfo=UTC), datetime.now(UTC)
        )

    bars_per_day = 86_400 // TIMEFRAME_SECONDS[args.timeframe]
    rows: dict[str, tuple[list[tuple[rb.Curve, float]], list[ReplayResult] | None]] = {}
    folds: list[RollingFold] = []
    for name in names:
        for policy in get_args(ExitPolicy):
            print(f"[..] {name} / {policy}", flush=True)
            results = run_rolling_walk_forward(
                candles,
                instrument=instrument,
                timeframe=args.timeframe,
                exchange_code=exchange_code,
                rules=CONSERVATIVE_V1_RULES,
                initial_equity=INITIAL_EQUITY,
                train_bars=TRAIN_DAYS * bars_per_day,
                test_bars=TEST_DAYS * bars_per_day,
                signal_generator=RESEARCH_STRATEGIES[name],
                exit_policy=policy,
            )
            folds = [r.fold for r in results]
            tests = [r.test_result for r in results]
            rows[f"{name} / {policy}"] = (strategy_windows(tests), tests)
    rows["benchmark / hold_10pct"] = (benchmark_windows(candles, folds), None)

    span, years = tested_years(candles, folds)
    print(
        f"\n=== {args.symbol} {args.timeframe}: {len(folds)} test windows, "
        f"{span} ({years:.1f}y) ==="
    )
    print("-- 1. keeps making money? / 2. copes with crashes? --")
    for label, (windows, tests_or_none) in rows.items():
        print(rb.format_summary(label, rb.summarize_windows(windows, years=years)))
        if tests_or_none is not None:
            print(trade_line(tests_or_none))

    print("\n-- crashes (change of the compounded curve through each window) --")
    print(crash_header())
    print(market_crash_line(candles))
    for label, (windows, _) in rows.items():
        print(crash_line(label, windows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
