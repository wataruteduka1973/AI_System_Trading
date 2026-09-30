"""Compare exit policies (`signal` / `stop_loss` / `stop_and_target`) on the
two questions asked on 2026-09-30, with not losing money as the priority:

1. Which is more likely to keep making money? -- compounded return, how it
   splits between the first and second half of the history and the most
   recent 24 windows, and how much of it hinges on the 5 best windows.
2. Which copes better with sudden crashes? -- maximum drawdown of the chained
   curve, longest time underwater, worst day, worst 30-day window, worst
   single trade, and the result through each historical crash in `CRASHES`.

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
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import get_args

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
from app.trading.application.backtest_replay import ExitPolicy
from app.trading.application.backtest_walk_forward import (
    RollingFoldResult,
    run_rolling_walk_forward,
)
from app.trading.application.research_strategies import RESEARCH_STRATEGIES
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES

INITIAL_EQUITY = Decimal(1_000_000)
BENCHMARK_EXPOSURE = 0.10
TRAIN_DAYS = 90
TEST_DAYS = 30

CRASHES: dict[str, tuple[str, str]] = {
    "2018-11 hash war": ("2018-11-14", "2018-12-15"),
    "2020-03 COVID": ("2020-03-08", "2020-03-16"),
    "2021-05 China ban": ("2021-05-12", "2021-05-23"),
    "2022-05 LUNA": ("2022-05-05", "2022-05-18"),
    "2022-06 3AC/Celsius": ("2022-06-10", "2022-06-20"),
    "2022-11 FTX": ("2022-11-06", "2022-11-12"),
    "2024-08 yen carry": ("2024-08-01", "2024-08-07"),
}
"""Historical BTC crashes, fixed before looking at any strategy's result.
Dates are UTC, inclusive."""


def _crash_bounds(start: str, end: str) -> tuple[datetime, datetime]:
    return (
        datetime.fromisoformat(start).replace(tzinfo=UTC),
        datetime.fromisoformat(end).replace(tzinfo=UTC) + timedelta(days=1),
    )


def _strategy_windows(results: Sequence[RollingFoldResult]) -> list[tuple[rb.Curve, float]]:
    initial = float(INITIAL_EQUITY)
    return [
        (
            [(time, float(equity) / initial) for time, equity in r.test_result.equity_curve],
            float(r.test_result.ending_equity) / initial,
        )
        for r in results
    ]


def _benchmark_windows(
    candles: Sequence[Candle], results: Sequence[RollingFoldResult]
) -> list[tuple[rb.Curve, float]]:
    windows: list[tuple[rb.Curve, float]] = []
    for r in results:
        test = candles[r.fold.train_end : r.fold.test_end]
        start = float(test[0].close)
        curve = [
            (c.close_time, 1 + BENCHMARK_EXPOSURE * (float(c.close) / start - 1)) for c in test
        ]
        windows.append((curve, curve[-1][1]))
    return windows


def _compounded(windows: Sequence[tuple[rb.Curve, float]]) -> float:
    level = 1.0
    for _, ending in windows:
        level *= ending
    return level - 1


def _summary_line(name: str, windows: list[tuple[rb.Curve, float]], years: float) -> str:
    curve = rb.chain_windows(windows)
    total = _compounded(windows)
    half = len(windows) // 2
    drawdown = rb.max_drawdown(curve)
    worst_day = rb.worst_daily_return(curve)
    returns = [ending - 1 for _, ending in windows]
    concentration = rb.top_share(returns, top_n=5)
    return (
        f"{name:<32} total={total:>+7.1%} cagr={(1 + total) ** (1 / years) - 1:>+6.2%} "
        f"1st-half={_compounded(windows[:half]):>+7.1%} "
        f"2nd-half={_compounded(windows[half:]):>+7.1%} "
        f"last24={_compounded(windows[-24:]):>+6.1%} "
        f"top5-share={'-' if concentration is None else f'{concentration:.0%}':>5} | "
        f"maxDD={drawdown.depth:>5.1%} "
        f"underwater={rb.longest_underwater(curve).days:>4}d "
        f"worst-day={'-' if worst_day is None else f'{worst_day:+.2%}':>7} "
        f"worst-window={min(returns):>+6.2%} "
        f"win-windows={sum(r > 0 for r in returns) / len(returns):>4.0%}"
    )


def _trade_line(results: Sequence[RollingFoldResult]) -> str:
    trades = [t for r in results for t in r.test_result.trades]
    if not trades:
        return "    trades=0"
    worst = min(float((t.realized_pnl - t.fees) / INITIAL_EQUITY) for t in trades)
    counts = {
        reason: sum(t.exit_reason == reason for t in trades)
        for reason in ("signal", "stop_loss", "take_profit")
    }
    return (
        f"    trades={len(trades)} worst-trade={worst:+.2%} of equity "
        f"exits: signal={counts['signal']} stop_loss={counts['stop_loss']} "
        f"take_profit={counts['take_profit']}"
    )


def _crash_cells(windows: list[tuple[rb.Curve, float]]) -> str:
    curve = rb.chain_windows(windows)
    cells = []
    for start, end in CRASHES.values():
        lo, hi = _crash_bounds(start, end)
        change = rb.window_return(curve, start=lo, end=hi)
        cells.append(f"{'-' if change is None else f'{change:+.2%}':>9}")
    return " ".join(cells)


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
    rows: dict[str, tuple[list[tuple[rb.Curve, float]], Sequence[RollingFoldResult] | None]] = {}
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
            rows[f"{name} / {policy}"] = (_strategy_windows(results), results)
    rows["benchmark / hold_10pct"] = (_benchmark_windows(candles, results), None)

    tested_from = candles[results[0].fold.train_end].open_time
    tested_to = candles[results[-1].fold.test_end - 1].close_time
    years = (tested_to - tested_from).days / 365.25

    print(
        f"\n=== {args.symbol} {args.timeframe}: {len(results)} test windows, "
        f"{tested_from:%Y-%m-%d}..{tested_to:%Y-%m-%d} ({years:.1f} years) ==="
    )
    print("-- 1. keeps making money? / 2. copes with crashes? --")
    for label, (windows, fold_results) in rows.items():
        print(_summary_line(label, windows, years))
        if fold_results is not None:
            print(_trade_line(fold_results))

    print("\n-- crashes (change of the compounded curve through each window) --")
    print(f"{'':<32} " + " ".join(f"{name[:9]:>9}" for name in CRASHES))
    market = []
    for start, end in CRASHES.values():
        lo, hi = _crash_bounds(start, end)
        inside = [c for c in candles if lo <= c.close_time <= hi]
        before = [c for c in candles if c.close_time < lo]
        change = float(inside[-1].close / before[-1].close - 1) if inside and before else None
        market.append(f"{'-' if change is None else f'{change:+.1%}':>9}")
    print(f"{'market (BTC itself)':<32} " + " ".join(market))
    for label, (windows, _) in rows.items():
        print(f"{label:<32} {_crash_cells(windows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
