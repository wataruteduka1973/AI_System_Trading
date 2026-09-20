"""Unit 5 (docs/plans/horizon4-lite-backtest.md): metrics calculation (pure) and
persistence (mocked Session, matching this codebase's existing convention -- see
e.g. tests/test_bot_lifecycle.py -- of not hitting a real DB in unit tests).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application import backtest_metrics as metrics_mod
from app.trading.application import backtest_replay as replay
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


def _trade(**overrides: object) -> replay.TradeRecord:
    now = datetime(2026, 9, 21, tzinfo=UTC)
    defaults: dict[str, object] = dict(
        sequence_no=1,
        side="sell",
        entry_time=now,
        exit_time=now + timedelta(minutes=1),
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        quantity=Decimal("10"),
        fees=Decimal("0"),
        realized_pnl=Decimal("100"),
    )
    defaults.update(overrides)
    return replay.TradeRecord(**defaults)


def _result(
    trades: list[replay.TradeRecord], equity_curve: list[tuple[datetime, Decimal]]
) -> replay.ReplayResult:
    ending_equity = equity_curve[-1][1] if equity_curve else Decimal(0)
    return replay.ReplayResult(
        trades=trades, ending_equity=ending_equity, ending_position=None, equity_curve=equity_curve
    )


_T0 = datetime(2026, 9, 21, tzinfo=UTC)


def _curve(*values: str) -> list[tuple[datetime, Decimal]]:
    return [(_T0 + timedelta(minutes=i), Decimal(v)) for i, v in enumerate(values)]


# ---- compute_metrics ----


def test_compute_metrics_with_no_trades_is_all_zero() -> None:
    result = _result([], _curve("1000", "1000"))
    metrics = metrics_mod.compute_metrics(result, initial_equity=Decimal("1000"))
    assert metrics.trade_count == 0
    assert metrics.win_count == 0
    assert metrics.loss_count == 0
    assert metrics.win_rate == Decimal(0)
    assert metrics.net_pnl == Decimal(0)
    assert metrics.profit_factor is None
    assert metrics.max_drawdown == Decimal(0)
    assert metrics.max_drawdown_pct == Decimal(0)


def test_compute_metrics_win_and_loss_are_fee_inclusive() -> None:
    win = _trade(realized_pnl=Decimal("100"), fees=Decimal("10"))  # net +90
    loss = _trade(realized_pnl=Decimal("-40"), fees=Decimal("5"))  # net -45
    result = _result([win, loss], _curve("1000", "1090", "1045"))

    metrics = metrics_mod.compute_metrics(result, initial_equity=Decimal("1000"))

    assert metrics.trade_count == 2
    assert metrics.win_count == 1
    assert metrics.loss_count == 1
    assert metrics.win_rate == Decimal("0.5")
    assert metrics.gross_profit == Decimal("90")
    assert metrics.gross_loss == Decimal("45")
    assert metrics.net_pnl == Decimal("45")
    assert metrics.total_fees == Decimal("15")
    assert metrics.profit_factor == Decimal("90") / Decimal("45")


def test_compute_metrics_profit_factor_is_none_without_losses() -> None:
    win = _trade(realized_pnl=Decimal("100"), fees=Decimal("0"))
    result = _result([win], _curve("1000", "1100"))
    metrics = metrics_mod.compute_metrics(result, initial_equity=Decimal("1000"))
    assert metrics.profit_factor is None


def test_compute_metrics_max_drawdown_from_equity_curve() -> None:
    # Peak 1200 at t=1, trough 900 at t=2 -> drawdown 300, 25% of peak. The trade
    # list is irrelevant to this calculation; only the curve matters.
    curve = _curve("1000", "1200", "900", "1000")
    result = _result([], curve)
    metrics = metrics_mod.compute_metrics(result, initial_equity=Decimal("1000"))
    assert metrics.max_drawdown == Decimal("300")
    assert metrics.max_drawdown_pct == Decimal("300") / Decimal("1200")


def test_compute_metrics_max_drawdown_accounts_for_initial_equity_as_the_first_peak() -> None:
    # Curve immediately drops below initial_equity without ever exceeding it.
    curve = _curve("900", "950")
    result = _result([], curve)
    metrics = metrics_mod.compute_metrics(result, initial_equity=Decimal("1000"))
    assert metrics.max_drawdown == Decimal("100")  # 1000 -> 900
    assert metrics.max_drawdown_pct == Decimal("0.1")


# ---- compare_to_baseline ----


def test_compare_to_baseline_diffs_each_metric() -> None:
    candidate = metrics_mod.compute_metrics(
        _result([_trade(realized_pnl=Decimal("100"), fees=Decimal("0"))], _curve("1000", "1100")),
        initial_equity=Decimal("1000"),
    )
    baseline = metrics_mod.compute_metrics(
        _result([_trade(realized_pnl=Decimal("50"), fees=Decimal("0"))], _curve("1000", "1050")),
        initial_equity=Decimal("1000"),
    )
    comparison = metrics_mod.compare_to_baseline(candidate, baseline)
    assert comparison.net_pnl_diff == Decimal("50")
    assert comparison.win_rate_diff == Decimal(0)  # both 100% of 1 trade


def test_compare_to_baseline_profit_factor_diff_is_none_when_either_side_is_none() -> None:
    all_wins = metrics_mod.compute_metrics(
        _result([_trade(realized_pnl=Decimal("100"), fees=Decimal("0"))], _curve("1000", "1100")),
        initial_equity=Decimal("1000"),
    )
    has_a_loss = metrics_mod.compute_metrics(
        _result(
            [
                _trade(realized_pnl=Decimal("100"), fees=Decimal("0")),
                _trade(realized_pnl=Decimal("-50"), fees=Decimal("0")),
            ],
            _curve("1000", "1100", "1050"),
        ),
        initial_equity=Decimal("1000"),
    )
    assert metrics_mod.compare_to_baseline(all_wins, has_a_loss).profit_factor_diff is None
    assert metrics_mod.compare_to_baseline(has_a_loss, all_wins).profit_factor_diff is None


# ---- summary_metrics_dict ----


def test_summary_metrics_dict_is_json_shaped_without_baseline() -> None:
    metrics = metrics_mod.compute_metrics(
        _result([_trade(realized_pnl=Decimal("100"), fees=Decimal("0"))], _curve("1000", "1100")),
        initial_equity=Decimal("1000"),
    )
    summary = metrics_mod.summary_metrics_dict(metrics)
    assert "baseline_comparison" not in summary
    assert summary["metrics"]["net_pnl"] == "100"
    assert summary["metrics"]["profit_factor"] is None


def test_summary_metrics_dict_includes_baseline_comparison_when_given() -> None:
    candidate = metrics_mod.compute_metrics(
        _result([_trade(realized_pnl=Decimal("100"), fees=Decimal("0"))], _curve("1000", "1100")),
        initial_equity=Decimal("1000"),
    )
    baseline = metrics_mod.compute_metrics(
        _result([_trade(realized_pnl=Decimal("50"), fees=Decimal("0"))], _curve("1000", "1050")),
        initial_equity=Decimal("1000"),
    )
    comparison = metrics_mod.compare_to_baseline(candidate, baseline)
    summary = metrics_mod.summary_metrics_dict(candidate, comparison)
    assert summary["baseline_comparison"]["net_pnl_diff"] == "50"
    assert summary["baseline_comparison"]["baseline"]["net_pnl"] == "50"


# ---- persist_backtest_run ----


def test_persist_backtest_run_adds_one_run_and_one_trade_row() -> None:
    db = MagicMock()
    trade = _trade()
    result = _result([trade], _curve("1000", "1100"))
    metrics = metrics_mod.compute_metrics(result, initial_equity=Decimal("1000"))

    run = metrics_mod.persist_backtest_run(
        db,
        workspace_id=uuid4(),
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        dataset_snapshot_id=uuid4(),
        instrument_id=uuid4(),
        code_version="test",
        parameters={"note": "unit test"},
        result=result,
        metrics=metrics,
    )

    assert run.status == "succeeded"
    assert run.summary_metrics["metrics"]["trade_count"] == 1
    added_types = [type(call.args[0]).__name__ for call in db.add.call_args_list]
    assert added_types == ["BacktestRun", "BacktestTrade"]
    db.flush.assert_called()


# ---- run_and_persist_backtest ----


def test_run_and_persist_backtest_without_baseline_has_no_comparison() -> None:
    db = MagicMock()
    candles = [_candle(Decimal("100"), 0), _candle(Decimal("100"), 1), _candle(Decimal("110"), 2)]

    def scripted(history: object) -> str:
        return {1: "buy", 2: "hold", 3: "sell"}[len(history)]  # type: ignore[arg-type]

    run = metrics_mod.run_and_persist_backtest(
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
        signal_generator=scripted,  # type: ignore[arg-type]
    )

    assert run.status == "succeeded"
    assert "baseline_comparison" not in run.summary_metrics
    assert run.summary_metrics["metrics"]["trade_count"] == 1


def test_run_and_persist_backtest_with_baseline_adds_comparison() -> None:
    db = MagicMock()
    candles = [_candle(Decimal("100"), 0), _candle(Decimal("100"), 1), _candle(Decimal("110"), 2)]

    def scripted(history: object) -> str:
        return {1: "buy", 2: "hold", 3: "sell"}[len(history)]  # type: ignore[arg-type]

    def always_hold(history: object) -> str:
        return "hold"

    run = metrics_mod.run_and_persist_backtest(
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
        signal_generator=scripted,  # type: ignore[arg-type]
        baseline_signal_generator=always_hold,  # type: ignore[arg-type]
    )

    assert "baseline_comparison" in run.summary_metrics
    assert run.summary_metrics["baseline_comparison"]["baseline"]["trade_count"] == 0
