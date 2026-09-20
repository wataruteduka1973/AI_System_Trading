"""Backtest evaluation metrics and persistence (docs/plans/horizon4-lite-backtest.md
Unit 5). `compute_metrics`/`compare_to_baseline`/`summary_metrics_dict` are pure
functions over a `backtest_replay.ReplayResult` (no `Session`); `persist_backtest_run`
is the one DB-writing function in this module, turning an already-completed replay
into `BacktestRun`/`BacktestTrade` rows (Unit 1's models) plus a JSON-serializable
`summary_metrics`.

Metric definitions follow `docs/concept/FXtrading_rebuild/06_AI学習と外部モデル探索設計.md`
§評価ゲート's list (手数料・spread・slippage込みの損益/最大DD/Profit Factor・勝率・
取引回数/baselineとの差), scoped to Horizon4-lite's MVP subset per ADR 0003
(`相場局面別の性能`/`時系列外検証とwalk-forward`/inference-time metrics are out of
scope here -- see the plan's Unit 6 and Horizon 6 itself).

**Fee-inclusive P&L**: every metric below uses `realized_pnl - fees` per trade (not
bare `realized_pnl`), since "手数料...込みの損益" is an explicit requirement, not an
optional refinement.

**`profit_factor` is `None`, not a fabricated number, when there are no losing
trades** (division by zero): gross_loss == 0 does not mean "infinitely good", it
means the ratio is undefined from this sample; callers must handle `None` rather
than assume a numeric comparison is always possible.

**Max drawdown** is computed from `ReplayResult.equity_curve` (added to Unit 4 for
this purpose) against a running peak seeded at the run's `initial_equity` -- not
just the equity value at each closed trade, which would understate drawdown while a
losing position is still open.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun, BacktestTrade
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application.backtest_replay import (
    BacktestSignalGenerator,
    ReplayResult,
    generate_dummy_signal,
    run_replay,
)


@dataclass(frozen=True)
class ReplayMetrics:
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    """0 when `trade_count == 0`."""
    gross_profit: Decimal
    """Sum of fee-inclusive P&L over winning trades (>= 0)."""
    gross_loss: Decimal
    """Sum of |fee-inclusive P&L| over losing trades, as a positive number (>= 0)."""
    net_pnl: Decimal
    """Sum of fee-inclusive P&L over every trade; equals `gross_profit - gross_loss`."""
    total_fees: Decimal
    profit_factor: Decimal | None
    """`gross_profit / gross_loss`, or `None` when `gross_loss == 0` (undefined, not
    infinite -- see module docstring)."""
    max_drawdown: Decimal
    """Peak-to-trough equity decline, in the account's currency units (>= 0)."""
    max_drawdown_pct: Decimal
    """Peak-to-trough equity decline as a fraction of the peak (>= 0)."""


@dataclass(frozen=True)
class BaselineComparison:
    candidate: ReplayMetrics
    baseline: ReplayMetrics
    net_pnl_diff: Decimal
    win_rate_diff: Decimal
    max_drawdown_pct_diff: Decimal
    profit_factor_diff: Decimal | None
    """`None` whenever either side's `profit_factor` is `None` (undefined minus a
    number is still undefined)."""


def compute_metrics(result: ReplayResult, initial_equity: Decimal) -> ReplayMetrics:
    net_pnl_per_trade = [trade.realized_pnl - trade.fees for trade in result.trades]
    wins = [pnl for pnl in net_pnl_per_trade if pnl > 0]
    losses = [pnl for pnl in net_pnl_per_trade if pnl < 0]

    gross_profit = sum(wins, Decimal(0))
    gross_loss = -sum(losses, Decimal(0))
    net_pnl = sum(net_pnl_per_trade, Decimal(0))
    total_fees = sum((trade.fees for trade in result.trades), Decimal(0))
    trade_count = len(result.trades)
    win_rate = Decimal(len(wins)) / Decimal(trade_count) if trade_count > 0 else Decimal(0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    peak = initial_equity
    max_drawdown = Decimal(0)
    max_drawdown_pct = Decimal(0)
    for _, equity in result.equity_curve:
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, drawdown / peak)

    return ReplayMetrics(
        trade_count=trade_count,
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        total_fees=total_fees,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
    )


def compare_to_baseline(candidate: ReplayMetrics, baseline: ReplayMetrics) -> BaselineComparison:
    profit_factor_diff = (
        candidate.profit_factor - baseline.profit_factor
        if candidate.profit_factor is not None and baseline.profit_factor is not None
        else None
    )
    return BaselineComparison(
        candidate=candidate,
        baseline=baseline,
        net_pnl_diff=candidate.net_pnl - baseline.net_pnl,
        win_rate_diff=candidate.win_rate - baseline.win_rate,
        max_drawdown_pct_diff=candidate.max_drawdown_pct - baseline.max_drawdown_pct,
        profit_factor_diff=profit_factor_diff,
    )


def _metrics_to_dict(metrics: ReplayMetrics) -> dict[str, object]:
    return {
        "trade_count": metrics.trade_count,
        "win_count": metrics.win_count,
        "loss_count": metrics.loss_count,
        "win_rate": str(metrics.win_rate),
        "gross_profit": str(metrics.gross_profit),
        "gross_loss": str(metrics.gross_loss),
        "net_pnl": str(metrics.net_pnl),
        "total_fees": str(metrics.total_fees),
        "profit_factor": (
            str(metrics.profit_factor) if metrics.profit_factor is not None else None
        ),
        "max_drawdown": str(metrics.max_drawdown),
        "max_drawdown_pct": str(metrics.max_drawdown_pct),
    }


def summary_metrics_dict(
    metrics: ReplayMetrics, baseline_comparison: BaselineComparison | None = None
) -> dict[str, object]:
    """Shaped for `backtest_run.summary_metrics` (JSONB) -- every `Decimal` is a
    `str` (matches `risk_gate.py`'s `rule_results` convention), so this round-trips
    through JSON without precision loss."""
    summary: dict[str, object] = {"metrics": _metrics_to_dict(metrics)}
    if baseline_comparison is not None:
        summary["baseline_comparison"] = {
            "baseline": _metrics_to_dict(baseline_comparison.baseline),
            "net_pnl_diff": str(baseline_comparison.net_pnl_diff),
            "win_rate_diff": str(baseline_comparison.win_rate_diff),
            "max_drawdown_pct_diff": str(baseline_comparison.max_drawdown_pct_diff),
            "profit_factor_diff": (
                str(baseline_comparison.profit_factor_diff)
                if baseline_comparison.profit_factor_diff is not None
                else None
            ),
        }
    return summary


def persist_backtest_run(
    db: Session,
    *,
    workspace_id: UUID,
    strategy_version_id: UUID,
    risk_profile_version_id: UUID,
    dataset_snapshot_id: UUID,
    instrument_id: UUID,
    code_version: str,
    parameters: dict[str, object],
    result: ReplayResult,
    metrics: ReplayMetrics,
    baseline_comparison: BaselineComparison | None = None,
) -> BacktestRun:
    """Writes one `BacktestRun` row plus one `BacktestTrade` row per
    `result.trades` entry, and the computed `summary_metrics`. Takes an
    already-completed `ReplayResult`/`ReplayMetrics` -- it does not call
    `run_replay`/`compute_metrics` itself, and does not run inside `_rollback_on_failure`
    the way `order_flow.py`'s functions do, since a backtest failing to persist is not
    a Paper Trading safety concern the way a live order is. Caller commits.

    `dataset_snapshot_id`/`strategy_version_id`/`risk_profile_version_id` are taken
    as given, not created here: which `StrategyVersion`/`RiskProfileVersion`/
    `DatasetSnapshot` a run belongs to is a decision for the run's caller, not
    something this persistence step should default on its own."""
    now = datetime.now(UTC)
    run = BacktestRun(
        workspace_id=workspace_id,
        strategy_version_id=strategy_version_id,
        risk_profile_version_id=risk_profile_version_id,
        dataset_snapshot_id=dataset_snapshot_id,
        parameters=parameters,
        code_version=code_version,
        status="running",
        started_at=now,
    )
    db.add(run)
    db.flush()

    for trade in result.trades:
        db.add(
            BacktestTrade(
                backtest_run_id=run.id,
                sequence_no=trade.sequence_no,
                instrument_id=instrument_id,
                side=trade.side,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                quantity=trade.quantity,
                fees=trade.fees,
                realized_pnl=trade.realized_pnl,
            )
        )

    run.summary_metrics = summary_metrics_dict(metrics, baseline_comparison)
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC)
    db.flush()
    db.refresh(run)
    return run


def run_and_persist_backtest(
    db: Session,
    candles: Sequence[Candle],
    *,
    workspace_id: UUID,
    strategy_version_id: UUID,
    risk_profile_version_id: UUID,
    dataset_snapshot_id: UUID,
    instrument: Instrument,
    timeframe: str,
    exchange_code: str,
    rules: dict,
    initial_equity: Decimal,
    code_version: str,
    spread: Decimal = Decimal(0),
    signal_generator: BacktestSignalGenerator = generate_dummy_signal,
    baseline_signal_generator: BacktestSignalGenerator | None = None,
) -> BacktestRun:
    """The single entry point that ties Units 4-5 together: replay `candles` once
    with `signal_generator`, optionally replay them a second time with
    `baseline_signal_generator` for comparison (both share every other parameter, so
    the only difference between the two runs is the signal source), compute metrics
    for both, and persist one `BacktestRun`. Passing no `baseline_signal_generator`
    (the default) skips the second replay and stores metrics with no baseline
    comparison -- comparing against a baseline is meaningful once there are two
    different strategies to compare (e.g. Chronos vs. `generate_dummy_signal`), not
    when `signal_generator` already *is* the baseline."""
    result = run_replay(
        candles,
        instrument=instrument,
        timeframe=timeframe,
        exchange_code=exchange_code,
        rules=rules,
        initial_equity=initial_equity,
        spread=spread,
        signal_generator=signal_generator,
    )
    metrics = compute_metrics(result, initial_equity)

    baseline_comparison = None
    if baseline_signal_generator is not None:
        baseline_result = run_replay(
            candles,
            instrument=instrument,
            timeframe=timeframe,
            exchange_code=exchange_code,
            rules=rules,
            initial_equity=initial_equity,
            spread=spread,
            signal_generator=baseline_signal_generator,
        )
        baseline_metrics = compute_metrics(baseline_result, initial_equity)
        baseline_comparison = compare_to_baseline(metrics, baseline_metrics)

    parameters: dict[str, object] = {
        "timeframe": timeframe,
        "exchange_code": exchange_code,
        "initial_equity": str(initial_equity),
        "spread": str(spread),
        "signal_generator": getattr(signal_generator, "__name__", repr(signal_generator)),
    }
    return persist_backtest_run(
        db,
        workspace_id=workspace_id,
        strategy_version_id=strategy_version_id,
        risk_profile_version_id=risk_profile_version_id,
        dataset_snapshot_id=dataset_snapshot_id,
        instrument_id=instrument.id,
        code_version=code_version,
        parameters=parameters,
        result=result,
        metrics=metrics,
        baseline_comparison=baseline_comparison,
    )
