"""Multi-asset diversification check (2026-09-30, per user request): the best
single-asset candidate so far -- 4h Donchian(55/20) with a stop-loss and no
take-profit -- run unchanged on every asset in `--symbols`, then combined with
capital split equally across the assets.

Per asset it prints the same measures as `compare_exit_policies.py`, which
doubles as a robustness check: the parameters were fixed on BTC, so an asset
where it fails is evidence against it. For the portfolio it prints the
compounded return, maximum drawdown, longest time underwater, worst day,
return per calendar year, each historical crash, and the average pairwise
correlation of the assets' monthly strategy returns (low correlation is what
makes diversification shorten drawdowns).

The asset list was fixed before any result: the early-2018 top-10 coins by
market cap that still trade against USDT on Binance. Picking only survivors
biases the result upward (survivorship bias).

Research only: nothing is persisted.

Run: python scripts/research/evaluate_portfolio.py [--symbols BTCUSDT,ETHUSDT,...] [--timeframe 4h]
     [--stop-slippage 0.005]
"""

import argparse
import itertools
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.db.session import SessionLocal
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.application.public_research import find_public_research_instrument
from app.trading.application import backtest_robustness as rb
from app.trading.application.backtest_provisioning import (
    _exchange_code_for_instrument,
    load_final_candles,
)
from app.trading.application.backtest_replay import ExitPolicy
from app.trading.application.backtest_walk_forward import run_rolling_walk_forward
from app.trading.application.research_report import (
    INITIAL_EQUITY,
    benchmark_windows,
    strategy_windows,
    tested_years,
    trade_line,
)
from app.trading.application.research_strategies import RESEARCH_STRATEGIES
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,XRPUSDT,ADAUSDT,LTCUSDT,BNBUSDT"
STRATEGY = "donchian_55_20"
EXIT_POLICY: ExitPolicy = "stop_loss"
TRAIN_DAYS = 90
TEST_DAYS = 30


def monthly_returns(curve: rb.Curve) -> dict[tuple[int, int], float]:
    month_end: dict[tuple[int, int], float] = {}
    for time, value in curve:
        month_end[(time.year, time.month)] = value
    months = sorted(month_end)
    return {
        month: month_end[month] / month_end[previous] - 1
        for previous, month in zip(months, months[1:], strict=False)
    }


def average_pairwise_correlation(curves: Sequence[rb.Curve]) -> float | None:
    monthly = [monthly_returns(curve) for curve in curves]
    correlations = []
    for a, b in itertools.combinations(monthly, 2):
        shared = sorted(set(a) & set(b))
        xs, ys = [a[m] for m in shared], [b[m] for m in shared]
        if len(shared) >= 12 and statistics.pstdev(xs) > 0 and statistics.pstdev(ys) > 0:
            correlations.append(statistics.correlation(xs, ys))
    return statistics.fmean(correlations) if correlations else None


def curve_line(label: str, curve: rb.Curve) -> str:
    years = (curve[-1][0] - curve[0][0]).days / 365.25
    total = curve[-1][1] / curve[0][1] - 1
    middle = curve[0][0] + (curve[-1][0] - curve[0][0]) / 2
    at_middle = [value for time, value in curve if time <= middle][-1]
    worst_day = rb.worst_daily_return(curve)
    return (
        f"{label:<28} cagr={(1 + total) ** (1 / years) - 1:>+6.2%} total={total:>+7.1%} "
        f"1st-half={at_middle / curve[0][1] - 1:>+7.1%} "
        f"2nd-half={curve[-1][1] / at_middle - 1:>+7.1%} | "
        f"maxDD={rb.max_drawdown(curve).depth:>5.1%} "
        f"underwater={rb.longest_underwater(curve).days:>4}d "
        f"worst-day={'-' if worst_day is None else f'{worst_day:+.2%}':>7}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument(
        "--stop-slippage",
        type=Decimal,
        default=Decimal(0),
        help="fill each triggered stop this fraction worse than its level, e.g. 0.005",
    )
    args = parser.parse_args()

    strategy_curves: dict[str, list[tuple[datetime, float]]] = {}
    hold_curves: dict[str, list[tuple[datetime, float]]] = {}
    for symbol in args.symbols.split(","):
        with SessionLocal() as db:
            instrument = find_public_research_instrument(db, symbol)
            if instrument is None:
                print(f"[NG] {symbol}: run scripts/fetch_binance_public_history.py first")
                return 1
            exchange_code = _exchange_code_for_instrument(db, instrument)
            candles = load_final_candles(
                db,
                instrument.id,
                args.timeframe,
                datetime(2000, 1, 1, tzinfo=UTC),
                datetime.now(UTC),
            )
        print(f"[..] {symbol}", flush=True)
        bars_per_day = 86_400 // TIMEFRAME_SECONDS[args.timeframe]
        results = run_rolling_walk_forward(
            candles,
            instrument=instrument,
            timeframe=args.timeframe,
            exchange_code=exchange_code,
            rules=CONSERVATIVE_V1_RULES,
            initial_equity=INITIAL_EQUITY,
            train_bars=TRAIN_DAYS * bars_per_day,
            test_bars=TEST_DAYS * bars_per_day,
            signal_generator=RESEARCH_STRATEGIES[STRATEGY],
            exit_policy=EXIT_POLICY,
            stop_slippage=args.stop_slippage,
        )
        folds = [r.fold for r in results]
        tests = [r.test_result for r in results]
        windows = strategy_windows(tests)
        hold = benchmark_windows(candles, folds)
        span, years = tested_years(candles, folds)
        print(f"=== {symbol} {span} ({years:.1f}y) ===")
        print(rb.format_summary(f"{symbol} strategy", rb.summarize_windows(windows, years=years)))
        print(trade_line(tests))
        print(rb.format_summary(f"{symbol} hold_10pct", rb.summarize_windows(hold, years=years)))
        strategy_curves[symbol] = rb.chain_windows(windows)
        hold_curves[symbol] = rb.chain_windows(hold)

    portfolio = rb.equal_weight_portfolio(list(strategy_curves.values()))
    hold_portfolio = rb.equal_weight_portfolio(list(hold_curves.values()))
    print(f"\n=== equal-weight portfolio of {len(strategy_curves)} assets ===")
    print(curve_line("portfolio strategy", portfolio))
    print(curve_line("portfolio hold_10pct", hold_portfolio))
    correlation = average_pairwise_correlation(list(strategy_curves.values()))
    print(
        "average pairwise correlation of monthly strategy returns: "
        + ("-" if correlation is None else f"{correlation:.2f}")
    )

    print("\n-- return per calendar year --")
    strategy_years = rb.yearly_returns(portfolio)
    hold_years = rb.yearly_returns(hold_portfolio)
    for year in sorted(strategy_years):
        print(
            f"  {year}: strategy={strategy_years[year]:>+7.2%} "
            f"hold_10pct={hold_years.get(year, 0.0):>+7.2%}"
        )

    print("\n-- crashes --")
    for name, change in rb.crash_returns(portfolio, rb.MARKET_CRASHES).items():
        hold_change = rb.crash_returns(hold_portfolio, {name: rb.MARKET_CRASHES[name]})[name]
        print(
            f"  {name:<22} strategy={'-' if change is None else f'{change:+.2%}':>7} "
            f"hold_10pct={'-' if hold_change is None else f'{hold_change:+.2%}':>7}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
