"""Walk-forward / out-of-sample split (docs/plans/horizon4-lite-backtest.md Unit 6).

The plan's own description of this unit is deliberately minimal: "指定した期間を
訓練用/検証用に分けて同じハーネスを2回呼ぶ形で実現する" (split the period into
train/test, call the same harness twice) -- a single chronological split, not
rolling multi-fold walk-forward. `generate_dummy_signal` has no parameters to fit on
a train window (it is a fixed rule), so today this mechanism mostly exercises the
*infrastructure* -- comparing in-sample vs. out-of-sample performance on two
disjoint, chronologically-ordered windows -- ahead of Chronos, where a train window
will actually mean something (model fitting) and out-of-sample degradation is the
whole point of running this at all.

**Look-ahead bias**: already covered by Unit 4's own test
(`test_run_replay_never_shows_the_signal_generator_a_future_candle` in
tests/test_backtest_replay.py) at the single-replay level; splitting the candle
series here does not introduce a new look-ahead risk of its own -- `test_candles`
never includes anything from `train_candles`, so there is nothing from the train
window for the test replay to see even if it wanted to. This module's own test
suite checks the split itself (no overlap, chronological order) rather than
duplicating Unit 4's look-ahead test.

**Same `initial_equity` for both windows, not train's ending equity carried into
test**: walk-forward evaluates "how does this (fixed, in this MVP) strategy perform
on unseen data" -- the train and test windows need to be evaluated on equal terms
(same risk-sizing baseline) to be comparable at all; compounding train's result into
test's starting capital would conflate "did it perform differently out-of-sample"
with "did it just have more or less capital to size positions with."

**Warm-up history**: the test window's first ~100 bars inherit the same ATR
warm-up limitation `backtest_replay.py`'s own docstring already documents (no
candles before `test_candles[0]` are available to it) -- not a new gap Unit 6
introduces, just the same one, now visible twice.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application.backtest_metrics import (
    ReplayMetrics,
    compute_metrics,
    persist_backtest_run,
)
from app.trading.application.backtest_replay import (
    BacktestSignalGenerator,
    ReplayResult,
    generate_dummy_signal,
    run_replay,
)


class WalkForwardError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WalkForwardResult:
    split_index: int
    """Index into the original candle sequence where the test window begins (also
    `len(train_candles)`)."""
    train_result: ReplayResult
    train_metrics: ReplayMetrics
    test_result: ReplayResult
    test_metrics: ReplayMetrics


def split_candles_for_walk_forward(
    candles: Sequence[Candle], *, train_ratio: Decimal = Decimal("0.7")
) -> tuple[Sequence[Candle], Sequence[Candle]]:
    """Split `candles` chronologically into (train, test) with no overlap -- the
    first `train_ratio` fraction of bars, then the rest. Raises `WalkForwardError`
    rather than silently returning an empty window if `train_ratio` would leave
    either side with no candles at all."""
    if not Decimal(0) < train_ratio < Decimal(1):
        raise WalkForwardError(
            "invalid_train_ratio", "train_ratio must be strictly between 0 and 1"
        )
    split_index = int(Decimal(len(candles)) * train_ratio)
    if split_index <= 0 or split_index >= len(candles):
        raise WalkForwardError(
            "insufficient_candles",
            f"train_ratio={train_ratio} leaves an empty train or test window for "
            f"{len(candles)} candles",
        )
    return candles[:split_index], candles[split_index:]


def run_walk_forward(
    candles: Sequence[Candle],
    *,
    instrument: Instrument,
    timeframe: str,
    exchange_code: str,
    rules: dict,
    initial_equity: Decimal,
    spread: Decimal = Decimal(0),
    signal_generator: BacktestSignalGenerator = generate_dummy_signal,
    train_ratio: Decimal = Decimal("0.7"),
) -> WalkForwardResult:
    train_candles, test_candles = split_candles_for_walk_forward(candles, train_ratio=train_ratio)

    def _replay(window: Sequence[Candle]) -> tuple[ReplayResult, ReplayMetrics]:
        result = run_replay(
            window,
            instrument=instrument,
            timeframe=timeframe,
            exchange_code=exchange_code,
            rules=rules,
            initial_equity=initial_equity,
            spread=spread,
            signal_generator=signal_generator,
        )
        return result, compute_metrics(result, initial_equity)

    train_result, train_metrics = _replay(train_candles)
    test_result, test_metrics = _replay(test_candles)

    return WalkForwardResult(
        split_index=len(train_candles),
        train_result=train_result,
        train_metrics=train_metrics,
        test_result=test_result,
        test_metrics=test_metrics,
    )


def run_and_persist_walk_forward(
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
    train_ratio: Decimal = Decimal("0.7"),
) -> tuple[BacktestRun, BacktestRun]:
    """Persists the train and test windows as two separate `BacktestRun` rows
    (each with its own `BacktestTrade` rows and `summary_metrics`), linked to each
    other via `parameters` (`walk_forward_role`/`walk_forward_split_index`/
    `walk_forward_counterpart_run_id`) rather than a new column -- Unit 1's schema
    has no dedicated walk-forward-pair concept, and adding one is out of this unit's
    "minimal orchestration" scope. Returns `(train_run, test_run)`."""
    wf_result = run_walk_forward(
        candles,
        instrument=instrument,
        timeframe=timeframe,
        exchange_code=exchange_code,
        rules=rules,
        initial_equity=initial_equity,
        spread=spread,
        signal_generator=signal_generator,
        train_ratio=train_ratio,
    )

    base_parameters: dict[str, object] = {
        "timeframe": timeframe,
        "exchange_code": exchange_code,
        "initial_equity": str(initial_equity),
        "spread": str(spread),
        "signal_generator": getattr(signal_generator, "__name__", repr(signal_generator)),
        "walk_forward_train_ratio": str(train_ratio),
        "walk_forward_split_index": wf_result.split_index,
    }

    train_run = persist_backtest_run(
        db,
        workspace_id=workspace_id,
        strategy_version_id=strategy_version_id,
        risk_profile_version_id=risk_profile_version_id,
        dataset_snapshot_id=dataset_snapshot_id,
        instrument_id=instrument.id,
        code_version=code_version,
        parameters={**base_parameters, "walk_forward_role": "train"},
        result=wf_result.train_result,
        metrics=wf_result.train_metrics,
    )
    test_run = persist_backtest_run(
        db,
        workspace_id=workspace_id,
        strategy_version_id=strategy_version_id,
        risk_profile_version_id=risk_profile_version_id,
        dataset_snapshot_id=dataset_snapshot_id,
        instrument_id=instrument.id,
        code_version=code_version,
        parameters={
            **base_parameters,
            "walk_forward_role": "test",
            "walk_forward_counterpart_run_id": str(train_run.id),
        },
        result=wf_result.test_result,
        metrics=wf_result.test_metrics,
    )
    train_run.parameters = {
        **train_run.parameters,
        "walk_forward_counterpart_run_id": str(test_run.id),
    }
    db.flush()

    return train_run, test_run
