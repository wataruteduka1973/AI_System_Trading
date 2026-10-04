"""Decision-timeframe comparison (2026-10-04, per user request): does deciding on a
timeframe other than 4h change profit and loss for the adopted design -- 6-asset
equal-weight portfolio, Donchian breakout, fixed 2xATR stop, no take-profit?

Fixed before looking at any result (docs/plans/decision-timeframes.md):
- decision timeframes 1h, 2h, 4h, 6h, 8h, 12h, 1d (2h/6h/8h/12h built from 1h
  bars -- see `app/trading/application/bar_aggregation.py`);
- A ("same bars"): Donchian 55/20 bars on every timeframe;
- B ("same horizon"): 4h's 55/20 bars (220h/80h) converted to each timeframe,
  rounded half up: 1h 220/80, 2h 110/40, 4h 55/20, 6h 37/13, 8h 28/10,
  12h 18/7, 1d 9/3;
- stop fills 0.25% worse than the stop (the realistic level, see
  docs/plans/multi-asset-diversification.md); the Risk Gate's ATR(14) stop is
  left as is, so the stop distance scales with the timeframe -- part of what
  "deciding on another timeframe" means;
- the same rolling walk-forward (90-day train / 30-day test) per asset, then the
  equal-weight portfolio.

To guard against picking a lucky timeframe, a result only counts if it holds
across assets and changes smoothly between neighbouring timeframes.

Research only: nothing is persisted. Needs 1h, 4h and 1d history for every
symbol (`scripts/fetch_binance_public_history.py --timeframes 1h,4h,1d`).

Run: python scripts/research/compare_decision_timeframes.py [--workers 6]
"""

import argparse
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.db.session import SessionLocal
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.application.public_research import find_public_research_instrument
from app.models.market_data import Candle
from app.trading.application import backtest_robustness as rb
from app.trading.application.backtest_provisioning import (
    _exchange_code_for_instrument,
    load_final_candles,
)
from app.trading.application.backtest_walk_forward import run_rolling_walk_forward
from app.trading.application.bar_aggregation import aggregate_bars
from app.trading.application.donchian_breakout_signal import generate_donchian_breakout_signal
from app.trading.application.research_report import INITIAL_EQUITY, strategy_windows, tested_years
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES
from app.trading.application.signal_action import SignalAction

SYMBOLS = ("BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT", "BNBUSDT")
HOURS = {"1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12, "1d": 24}
SAME_HORIZON = {"1h": (220, 80), "2h": (110, 40), "4h": (55, 20), "6h": (37, 13),
                "8h": (28, 10), "12h": (18, 7), "1d": (9, 3)}  # fmt: skip
STOP_SLIPPAGE = Decimal("0.0025")
TRAIN_DAYS = 90
TEST_DAYS = 30


@dataclass(frozen=True)
class Job:
    symbol: str
    timeframe: str
    variant: str  # "A" same bars, "B" same horizon
    entry: int
    exit: int


@dataclass(frozen=True)
class JobResult:
    job: Job
    curve: list[tuple[datetime, float]]
    annual_return: float
    max_drawdown: float
    trades: int
    years: float


def _decision_bars(symbol: str, timeframe: str) -> tuple[list[Candle], object, str]:
    with SessionLocal() as db:
        instrument = find_public_research_instrument(db, symbol)
        if instrument is None:
            raise RuntimeError(f"no public price instrument for {symbol}")
        exchange_code = _exchange_code_for_instrument(db, instrument)
        stored = "1h" if timeframe not in ("1h", "4h", "1d") else timeframe
        candles = load_final_candles(
            db, instrument.id, stored, datetime(2000, 1, 1, tzinfo=UTC), datetime.now(UTC)
        )
        db.expunge_all()
    if stored != timeframe:
        candles = aggregate_bars(candles, hours_per_bar=HOURS[timeframe])
    return candles, instrument, exchange_code


def run_job(job: Job) -> JobResult:
    candles, instrument, exchange_code = _decision_bars(job.symbol, job.timeframe)

    def donchian(history: Sequence[Candle]) -> SignalAction:
        return generate_donchian_breakout_signal(
            history, entry_period=job.entry, exit_period=job.exit
        )

    bars_per_day = 24 // HOURS[job.timeframe]
    results = run_rolling_walk_forward(
        candles,
        instrument=instrument,  # type: ignore[arg-type]
        # run_replay reads the timeframe only to measure data delay, which is always
        # 0 in a replay (each bar is evaluated at its own close), so aggregated bars
        # can use an existing label.
        timeframe=job.timeframe if job.timeframe in TIMEFRAME_SECONDS else "1h",
        exchange_code=exchange_code,
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=INITIAL_EQUITY,
        train_bars=TRAIN_DAYS * bars_per_day,
        test_bars=TEST_DAYS * bars_per_day,
        signal_generator=donchian,
        exit_policy="stop_loss",
        stop_slippage=STOP_SLIPPAGE,
    )
    tests = [r.test_result for r in results]
    windows = strategy_windows(tests)
    _, years = tested_years(candles, [r.fold for r in results])
    summary = rb.summarize_windows(windows, years=years)
    return JobResult(
        job=job,
        curve=rb.chain_windows(windows),
        annual_return=summary.annual_return,
        max_drawdown=summary.max_drawdown,
        trades=sum(len(t.trades) for t in tests),
        years=years,
    )


def _jobs() -> list[Job]:
    jobs = []
    for timeframe in HOURS:
        for symbol in SYMBOLS:
            jobs.append(Job(symbol, timeframe, "A", 55, 20))
            if timeframe != "4h":  # B is identical to A on 4h
                entry, exit_ = SAME_HORIZON[timeframe]
                jobs.append(Job(symbol, timeframe, "B", entry, exit_))
    return jobs


def _portfolio_line(label: str, results: list[JobResult]) -> str:
    curve = rb.equal_weight_portfolio([r.curve for r in results])
    years = (curve[-1][0] - curve[0][0]).days / 365.25
    total = curve[-1][1] / curve[0][1] - 1
    worst_day = rb.worst_daily_return(curve)
    trades_per_year = sum(r.trades / r.years for r in results)
    per_asset = " ".join(
        f"{r.job.symbol[:3]}={r.annual_return:+.1%}/{r.max_drawdown:.0%}" for r in results
    )
    return (
        f"{label:<14} cagr={(1 + total) ** (1 / years) - 1:>+6.2%} "
        f"maxDD={rb.max_drawdown(curve).depth:>5.1%} "
        f"underwater={rb.longest_underwater(curve).days:>4}d "
        f"worst-day={'-' if worst_day is None else f'{worst_day:+.2%}':>7} "
        f"trades/yr={trades_per_year:>5.0f} | {per_asset}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args()

    jobs = _jobs()
    done: dict[tuple[str, str], list[JobResult]] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_job, job): job for job in jobs}
        for count, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            key = (result.job.variant, result.job.timeframe)
            done.setdefault(key, []).append(result)
            print(f"[{count}/{len(jobs)}] {result.job}", flush=True)

    print(f"\n=== decision timeframes, 6-asset portfolio, stop slippage {STOP_SLIPPAGE:%} ===")
    print("per asset: annual return / max drawdown")
    for variant, title in (("A", "A: same bars (55/20)"), ("B", "B: same horizon (~220h/80h)")):
        print(f"-- {title} --")
        for timeframe in HOURS:
            key = (variant, timeframe) if (variant, timeframe) in done else ("A", timeframe)
            results = sorted(done[key], key=lambda r: SYMBOLS.index(r.job.symbol))
            entry, exit_ = results[0].job.entry, results[0].job.exit
            print(_portfolio_line(f"{timeframe} {entry}/{exit_}", results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
