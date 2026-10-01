"""Report helpers shared by the research scripts (`scripts/compare_exit_policies.py`,
`scripts/evaluate_confluence.py`): turning walk-forward test windows into the
compounded curves of `backtest_robustness.py`, the 10%-hold benchmark, and the
report lines. Research only -- nothing here is used by live/paper execution.
"""

from collections.abc import Sequence
from decimal import Decimal

from app.models.market_data import Candle
from app.trading.application import backtest_robustness as rb
from app.trading.application.backtest_replay import ReplayResult
from app.trading.application.backtest_walk_forward import RollingFold

INITIAL_EQUITY = Decimal(1_000_000)
BENCHMARK_EXPOSURE = 0.10
"""One order's size under the Risk Gate's Binance order limit."""


def strategy_windows(results: Sequence[ReplayResult]) -> list[tuple[rb.Curve, float]]:
    initial = float(INITIAL_EQUITY)
    return [
        (
            [(time, float(equity) / initial) for time, equity in r.equity_curve],
            float(r.ending_equity) / initial,
        )
        for r in results
    ]


def benchmark_windows(
    candles: Sequence[Candle], folds: Sequence[RollingFold]
) -> list[tuple[rb.Curve, float]]:
    prices = [
        [(c.close_time, float(c.close)) for c in candles[f.train_end : f.test_end]] for f in folds
    ]
    return rb.hold_windows(prices, BENCHMARK_EXPOSURE)


def tested_years(candles: Sequence[Candle], folds: Sequence[RollingFold]) -> tuple[str, float]:
    start = candles[folds[0].train_end].open_time
    end = candles[folds[-1].test_end - 1].close_time
    return f"{start:%Y-%m-%d}..{end:%Y-%m-%d}", (end - start).days / 365.25


def trade_line(results: Sequence[ReplayResult]) -> str:
    trades = [t for r in results for t in r.trades]
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


def crash_line(label: str, windows: Sequence[tuple[rb.Curve, float]]) -> str:
    changes = rb.crash_returns(rb.chain_windows(windows), rb.MARKET_CRASHES)
    cells = ("-" if c is None else f"{c:+.2%}" for c in changes.values())
    return f"{label:<34} " + " ".join(f"{cell:>9}" for cell in cells)


def market_crash_line(candles: Sequence[Candle]) -> str:
    prices = [(c.close_time, float(c.close)) for c in candles]
    changes = rb.crash_returns(prices, rb.MARKET_CRASHES)
    cells = ("-" if c is None else f"{c:+.1%}" for c in changes.values())
    return f"{'market (BTC itself)':<34} " + " ".join(f"{cell:>9}" for cell in cells)


def crash_header() -> str:
    return f"{'':<34} " + " ".join(f"{name[:9]:>9}" for name in rb.MARKET_CRASHES)
