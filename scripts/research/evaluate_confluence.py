"""Evaluate confluence filters on top of Donchian(55/20) + stop-loss
(docs/plans/confluence-filters.md): every one of the 12 fixed filter
combinations, and a walk-forward that picks one per fold from its train
window alone, on the same folds (360-day train / 30-day test), against the
10%-hold benchmark. Reports the same "keeps making money / copes with crashes"
measures as `compare_exit_policies.py`, plus how often each combination was
picked.

Research only: nothing is persisted.

Run: python scripts/research/evaluate_confluence.py [--symbol BTCUSDT] [--timeframe 4h]
"""

import argparse
from collections import Counter
from datetime import UTC, datetime
from functools import partial

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
    run_selected_walk_forward,
)
from app.trading.application.confluence_signal import (
    ALL_FILTER_COMBINATIONS,
    build_indicator_table,
    make_confluence_signal,
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
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES

TRAIN_DAYS = 360
TEST_DAYS = 30
EXIT_POLICY: ExitPolicy = "stop_loss"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="4h")
    args = parser.parse_args()

    with SessionLocal() as db:
        instrument = find_public_research_instrument(db, args.symbol)
        if instrument is None:
            print(f"[NG] no {RESEARCH_EXCHANGE_CODE} instrument for {args.symbol}")
            return 1
        exchange_code = _exchange_code_for_instrument(db, instrument)
        candles = load_final_candles(
            db, instrument.id, args.timeframe, datetime(2000, 1, 1, tzinfo=UTC), datetime.now(UTC)
        )

    table = build_indicator_table(candles)
    candidates = {f.name: make_confluence_signal(table, f) for f in ALL_FILTER_COMBINATIONS}
    bars_per_day = 86_400 // TIMEFRAME_SECONDS[args.timeframe]
    fixed_walk_forward = partial(
        run_rolling_walk_forward,
        candles,
        instrument=instrument,
        timeframe=args.timeframe,
        exchange_code=exchange_code,
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=INITIAL_EQUITY,
        train_bars=TRAIN_DAYS * bars_per_day,
        test_bars=TEST_DAYS * bars_per_day,
        exit_policy=EXIT_POLICY,
    )

    rows: dict[str, list[ReplayResult]] = {}
    folds: list[RollingFold] = []
    for name, generator in candidates.items():
        print(f"[..] fixed {name}", flush=True)
        fixed = fixed_walk_forward(signal_generator=generator)
        rows[f"fixed {name}"] = [r.test_result for r in fixed]
        folds = [r.fold for r in fixed]

    print("[..] selected per fold from its train window", flush=True)
    selected = run_selected_walk_forward(
        candles,
        candidates=candidates,
        instrument=instrument,
        timeframe=args.timeframe,
        exchange_code=exchange_code,
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=INITIAL_EQUITY,
        train_bars=TRAIN_DAYS * bars_per_day,
        test_bars=TEST_DAYS * bars_per_day,
        exit_policy=EXIT_POLICY,
    )
    rows["selected (train window)"] = [r.test_result for r in selected]

    span, years = tested_years(candles, folds)
    print(
        f"\n=== {args.symbol} {args.timeframe} donchian_55_20 + {EXIT_POLICY}: "
        f"{len(folds)} test windows, {span} ({years:.1f}y), train {TRAIN_DAYS}d ==="
    )
    all_windows = {label: strategy_windows(tests) for label, tests in rows.items()}
    all_windows["benchmark / hold_10pct"] = benchmark_windows(candles, folds)
    for label, windows in all_windows.items():
        print(rb.format_summary(label, rb.summarize_windows(windows, years=years)))
        if label in rows:
            print(trade_line(rows[label]))

    print("\n-- how often each combination was selected --")
    for name, count in Counter(r.chosen for r in selected).most_common():
        print(f"  {name:<32} {count:>3}")

    print("\n-- crashes (change of the compounded curve through each window) --")
    print(crash_header())
    print(market_crash_line(candles))
    for label, windows in all_windows.items():
        print(crash_line(label, windows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
