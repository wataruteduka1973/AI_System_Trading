"""Unit 6 (docs/plans/horizon4-lite-backtest.md): the train/test split itself, plus
persistence of both windows as linked `BacktestRun` rows (mocked Session, matching
this codebase's existing convention). Look-ahead bias at the single-replay level is
already covered by tests/test_backtest_replay.py -- see this module's own docstring
for why the split does not introduce a new instance of that risk.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application import backtest_replay as replay
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


# ---- split_candles_rolling ----


def test_rolling_split_slides_by_test_bars_and_drops_the_partial_tail() -> None:
    folds = wf.split_candles_rolling(25, train_bars=10, test_bars=5)
    assert [(f.index, f.train_start, f.train_end, f.test_end) for f in folds] == [
        (0, 0, 10, 15),
        (1, 5, 15, 20),
        (2, 10, 20, 25),
    ]
    # 27 candles: the last 2 bars are too few for a full test window and are dropped.
    assert len(wf.split_candles_rolling(27, train_bars=10, test_bars=5)) == 3


def test_rolling_split_test_windows_never_overlap() -> None:
    folds = wf.split_candles_rolling(100, train_bars=30, test_bars=7)
    for previous, current in zip(folds, folds[1:], strict=False):
        assert previous.test_end == current.train_end


@pytest.mark.parametrize(("train_bars", "test_bars"), [(0, 5), (10, 0), (-1, 5)])
def test_rolling_split_rejects_non_positive_window_sizes(train_bars: int, test_bars: int) -> None:
    with pytest.raises(wf.WalkForwardError) as exc:
        wf.split_candles_rolling(100, train_bars=train_bars, test_bars=test_bars)
    assert exc.value.code == "invalid_window_size"


def test_rolling_split_rejects_too_few_candles_for_one_fold() -> None:
    with pytest.raises(wf.WalkForwardError) as exc:
        wf.split_candles_rolling(14, train_bars=10, test_bars=5)
    assert exc.value.code == "insufficient_candles"


# ---- run_rolling_walk_forward ----


def _run_rolling(candles: list[Candle], spy: object) -> list[wf.RollingFoldResult]:
    return wf.run_rolling_walk_forward(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=spy,  # type: ignore[arg-type]
        train_bars=10,
        test_bars=5,
    )


def test_rolling_walk_forward_evaluates_each_window_with_preceding_bars_as_warmup() -> None:
    # Close equals index, so the spy records exactly which bars each call saw.
    candles = [_candle(Decimal(i), i) for i in range(25)]
    evaluated: list[tuple[int, int]] = []  # (current bar, earliest bar in history)

    def spy(history: Sequence[Candle]) -> str:
        evaluated.append((int(history[-1].close), int(history[0].close)))
        return "hold"

    results = _run_rolling(candles, spy)

    assert [r.fold.index for r in results] == [0, 1, 2]
    evaluated_bars = [bar for bar, _ in evaluated]
    # fold 0: train 0-9, test 10-14; fold 1: train 5-14, test 15-19; fold 2: ...
    assert evaluated_bars == [
        *range(0, 10), *range(10, 15),
        *range(5, 15), *range(15, 20),
        *range(10, 20), *range(20, 25),
    ]  # fmt: skip
    # Every window's history reaches back to bar 0 (all bars are within the
    # history bound here) -- warm-up, not a fresh start at the window boundary --
    # and never past the bar being evaluated.
    assert all(earliest == 0 for _, earliest in evaluated)


def test_rolling_walk_forward_evaluates_every_window_from_the_same_initial_equity() -> None:
    candles = [_candle(Decimal("100"), i) for i in range(25)]

    results = _run_rolling(candles, lambda history: "hold")

    for r in results:
        assert r.train_result.ending_equity == Decimal("1000000")
        assert r.test_result.ending_equity == Decimal("1000000")
        assert len(r.train_result.equity_curve) == 10
        assert len(r.test_result.equity_curve) == 5


def test_rolling_walk_forward_warmup_is_bounded_by_the_history_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 4-bar history window means at most 3 warm-up bars before each window.
    monkeypatch.setattr(wf, "_HISTORY_WINDOW", 4)
    monkeypatch.setattr(replay, "_HISTORY_WINDOW", 4)
    candles = [_candle(Decimal(i), i) for i in range(25)]
    evaluated: list[tuple[int, int]] = []

    def spy(history: Sequence[Candle]) -> str:
        evaluated.append((int(history[-1].close), int(history[0].close)))
        return "hold"

    _run_rolling(candles, spy)

    assert all(earliest == max(0, bar - 3) for bar, earliest in evaluated)


def test_rolling_walk_forward_passes_the_exit_policy_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    real_run_replay = wf.run_replay

    def spy(*args: object, **kwargs: object) -> replay.ReplayResult:
        seen.append(kwargs["exit_policy"])
        return real_run_replay(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(wf, "run_replay", spy)
    candles = [_candle(Decimal("100"), i) for i in range(25)]
    wf.run_rolling_walk_forward(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=lambda history: "hold",
        train_bars=10,
        test_bars=5,
        exit_policy="stop_loss",
    )
    assert seen and all(policy == "stop_loss" for policy in seen)


# ---- run_selected_walk_forward ----


def _run_selected(
    candles: list[Candle], candidates: dict[str, object]
) -> list[wf.SelectedFoldResult]:
    return wf.run_selected_walk_forward(
        candles,
        candidates=candidates,  # type: ignore[arg-type]
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        train_bars=10,
        test_bars=5,
    )


def test_selection_scores_candidates_on_train_and_runs_only_the_winner_on_test() -> None:
    # Close equals index, so each spy records exactly which bars it was asked about.
    candles = [_candle(Decimal(i + 1), i) for i in range(25)]
    seen: dict[str, list[int]] = {"a": [], "b": []}

    def spy(name: str) -> object:
        def generate(history: Sequence[Candle]) -> str:
            seen[name].append(int(history[-1].close) - 1)
            return "hold"

        return generate

    results = _run_selected(candles, {"a": spy("a"), "b": spy("b")})

    # Equal (zero) scores everywhere: the first candidate wins every tie.
    assert [r.chosen for r in results] == ["a", "a", "a"]
    assert all(set(r.train_scores) == {"a", "b"} for r in results)
    # "b" was only ever replayed on train windows (the last one ends at bar 19);
    # "a" also ran on the test windows, up to bar 24.
    assert max(seen["b"]) == 19
    assert max(seen["a"]) == 24


def test_selection_picks_the_candidate_with_the_best_train_score() -> None:
    rising = [_candle(Decimal(100 + i), i) for i in range(25)]
    results = _run_selected(rising, {"flat": lambda history: "hold", "long": lambda history: "buy"})
    assert all(r.chosen == "long" for r in results)
    assert all(r.train_scores["long"] > r.train_scores["flat"] == 0 for r in results)
    assert all(r.test_result.ending_position is not None for r in results)


def test_loss_averse_score_penalizes_drawdown_twice_as_much_as_it_rewards_return() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    result = replay.ReplayResult(
        trades=[],
        ending_equity=Decimal("1100"),
        ending_position=None,
        equity_curve=[(t0, Decimal("1000")), (t0, Decimal("1200")), (t0, Decimal("1080"))],
    )
    # return +10%, max drawdown 10% (1200 -> 1080): 0.10 - 2 * 0.10
    assert wf.loss_averse_score(result, Decimal("1000")) == pytest.approx(-0.10)


def test_rolling_walk_forward_passes_stop_slippage_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    real_run_replay = wf.run_replay

    def spy(*args: object, **kwargs: object) -> replay.ReplayResult:
        seen.append(kwargs["stop_slippage"])
        return real_run_replay(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(wf, "run_replay", spy)
    wf.run_rolling_walk_forward(
        [_candle(Decimal("100"), i) for i in range(25)],
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=lambda history: "hold",
        train_bars=10,
        test_bars=5,
        exit_policy="stop_loss",
        stop_slippage=Decimal("0.005"),
    )
    assert seen and all(value == Decimal("0.005") for value in seen)


def test_rolling_walk_forward_passes_the_stop_monitor_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    real_run_replay = wf.run_replay

    def spy(*args: object, **kwargs: object) -> replay.ReplayResult:
        seen.append(kwargs["stop_monitor"])
        return real_run_replay(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(wf, "run_replay", spy)
    monitor: dict = {}
    wf.run_rolling_walk_forward(
        [_candle(Decimal("100"), i) for i in range(25)],
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=lambda history: "hold",
        train_bars=10,
        test_bars=5,
        exit_policy="stop_loss",
        stop_monitor=monitor,
    )
    assert seen and all(value is monitor for value in seen)
