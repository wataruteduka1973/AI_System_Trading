"""Unit 6 (docs/plans/horizon4-lite-backtest.md): the train/test split itself, plus
persistence of both windows as linked `BacktestRun` rows (mocked Session, matching
this codebase's existing convention). Look-ahead bias at the single-replay level is
already covered by tests/test_backtest_replay.py -- see this module's own docstring
for why the split does not introduce a new instance of that risk.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application import backtest_walk_forward as wf
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES


def _instrument(**overrides: object) -> Instrument:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        exchange_id=uuid4(),
        market_id=uuid4(),
        symbol="USD_JPY",
        base_asset="USD",
        quote_asset="JPY",
        price_scale=3,
        quantity_scale=0,
        tick_size=Decimal("0.001"),
        step_size=Decimal("1"),
        min_quantity=None,
        max_quantity=None,
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def _candle(close: Decimal, i: int) -> Candle:
    t = datetime(2026, 9, 21, 0, 0, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        source="test",
        is_final=True,
    )


# ---- split_candles_for_walk_forward ----


def test_split_is_chronological_and_non_overlapping() -> None:
    candles = [_candle(Decimal(i), i) for i in range(10)]
    train, test = wf.split_candles_for_walk_forward(candles, train_ratio=Decimal("0.7"))
    assert list(train) == candles[:7]
    assert list(test) == candles[7:]
    assert train[-1].close_time <= test[0].open_time  # back-to-back bars, no overlap


def test_split_rejects_a_ratio_that_leaves_the_test_window_empty() -> None:
    candles = [_candle(Decimal(i), i) for i in range(5)]
    with pytest.raises(wf.WalkForwardError) as exc:
        wf.split_candles_for_walk_forward(candles, train_ratio=Decimal("1"))
    assert exc.value.code == "invalid_train_ratio"


def test_split_rejects_too_few_candles_for_a_nonzero_train_ratio() -> None:
    candles = [_candle(Decimal("1"), 0)]  # a single candle: split_index rounds to 0
    with pytest.raises(wf.WalkForwardError) as exc:
        wf.split_candles_for_walk_forward(candles, train_ratio=Decimal("0.5"))
    assert exc.value.code == "insufficient_candles"


# ---- run_walk_forward ----


def test_run_walk_forward_evaluates_both_windows_independently() -> None:
    candles = [_candle(Decimal("100"), i) for i in range(10)]
    result = wf.run_walk_forward(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=lambda history: "hold",
        train_ratio=Decimal("0.7"),
    )
    assert result.split_index == 7
    assert result.train_metrics.trade_count == 0
    assert result.test_metrics.trade_count == 0
    # Both windows were evaluated from the same starting capital, independently.
    assert result.train_result.ending_equity == Decimal("1000000")
    assert result.test_result.ending_equity == Decimal("1000000")


# ---- run_and_persist_walk_forward ----


def test_run_and_persist_walk_forward_links_both_runs() -> None:
    db = MagicMock()
    candles = [_candle(Decimal("100"), i) for i in range(10)]

    train_run, test_run = wf.run_and_persist_walk_forward(
        db,
        candles,
        workspace_id=uuid4(),
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        dataset_snapshot_id=uuid4(),
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        code_version="test",
        signal_generator=lambda history: "hold",
        train_ratio=Decimal("0.7"),
    )

    assert train_run.parameters["walk_forward_role"] == "train"
    assert test_run.parameters["walk_forward_role"] == "test"
    assert train_run.parameters["walk_forward_split_index"] == 7
    assert test_run.parameters["walk_forward_counterpart_run_id"] == str(train_run.id)
    assert train_run.parameters["walk_forward_counterpart_run_id"] == str(test_run.id)
    assert train_run.status == "succeeded"
    assert test_run.status == "succeeded"
