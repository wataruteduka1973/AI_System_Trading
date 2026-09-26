"""`app/trading/application/backtest_provisioning.py` (Horizon 4 API/UI task,
2026-09-26): the one piece of docs/plans/horizon4-lite-backtest.md's Units 1-6
that had no implementation before this task (`ensure_dataset_snapshot`), plus
the orchestration (`run_backtest_for_workspace`) that ties already-tested
pieces (`run_and_persist_backtest`/`run_and_persist_walk_forward`, covered in
their own test files) together. Those pieces are monkeypatched here so this
file only exercises the orchestration itself: which one gets called, with what
arguments, and the no-candles error path.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.backtest import BacktestRun, DatasetSnapshot
from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application import backtest_provisioning as provisioning


def _instrument(**overrides: object) -> Instrument:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        exchange_id=uuid4(),
        market_id=uuid4(),
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_scale=2,
        quantity_scale=6,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.000001"),
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def _candle(instrument_id: object, i: int) -> Candle:
    t = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=instrument_id,
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
        source="test",
        is_final=True,
    )


# ---- ensure_dataset_snapshot ----


def test_ensure_dataset_snapshot_creates_a_new_snapshot_when_none_exists() -> None:
    db = MagicMock()
    db.scalar.side_effect = [None]  # no existing snapshot by checksum
    db.scalars.return_value.all.return_value = []  # no overlapping gaps
    instrument = _instrument()
    candles = [_candle(instrument.id, i) for i in range(3)]
    workspace_id = uuid4()
    from_time = candles[0].open_time
    to_time = candles[-1].close_time

    snapshot = provisioning.ensure_dataset_snapshot(
        db,
        workspace_id,
        instrument,
        timeframe="1m",
        from_time=from_time,
        to_time=to_time,
        candles=candles,
    )

    assert isinstance(snapshot, DatasetSnapshot)
    assert snapshot.row_count == 3
    assert snapshot.quality_report == {
        "gap_count": 0,
        "open_gap_count": 0,
        "total_missing_count": 0,
    }
    db.add.assert_called_once()
    db.flush.assert_called_once()


def test_ensure_dataset_snapshot_reuses_an_existing_snapshot_by_checksum() -> None:
    db = MagicMock()
    existing = DatasetSnapshot(id=uuid4(), workspace_id=uuid4(), checksum="whatever")
    db.scalar.return_value = existing
    instrument = _instrument()
    candles = [_candle(instrument.id, 0)]

    snapshot = provisioning.ensure_dataset_snapshot(
        db,
        uuid4(),
        instrument,
        timeframe="1m",
        from_time=candles[0].open_time,
        to_time=candles[0].close_time,
        candles=candles,
    )

    assert snapshot is existing
    db.add.assert_not_called()


# ---- run_backtest_for_workspace ----


def test_run_backtest_for_workspace_raises_when_no_candles_exist() -> None:
    db = MagicMock()
    db.scalars.return_value.all.return_value = []  # load_final_candles
    instrument = _instrument()

    with pytest.raises(provisioning.BacktestProvisioningError) as exc:
        provisioning.run_backtest_for_workspace(
            db,
            uuid4(),
            instrument,
            timeframe="1m",
            from_time=datetime(2026, 9, 1, tzinfo=UTC),
            to_time=datetime(2026, 9, 2, tzinfo=UTC),
            initial_equity=Decimal(10000),
        )
    assert exc.value.code == "no_candles"


def test_run_backtest_for_workspace_single_mode_calls_run_and_persist_backtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    instrument = _instrument()
    candles = [_candle(instrument.id, i) for i in range(5)]
    db.scalars.return_value.all.return_value = candles  # load_final_candles
    db.scalar.side_effect = [
        "BTCUSDT",  # exchange code lookup
        DatasetSnapshot(id=uuid4()),  # checksum lookup -- reuse existing, skip gap query
    ]

    from app.models.strategy import RiskProfileVersion, StrategyVersion

    strategy_version = StrategyVersion(id=uuid4(), strategy_id=uuid4(), version=1)
    risk_profile_version = RiskProfileVersion(id=uuid4(), risk_profile_id=uuid4(), version=1)
    monkeypatch.setattr(
        provisioning,
        "ensure_dummy_strategy_and_risk_profile",
        lambda db_, workspace_id: (strategy_version, risk_profile_version),
    )
    fake_run = BacktestRun(id=uuid4(), workspace_id=uuid4())
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        provisioning,
        "run_and_persist_backtest",
        lambda db_, candles_, **kwargs: calls.append(kwargs) or fake_run,
    )

    result = provisioning.run_backtest_for_workspace(
        db,
        uuid4(),
        instrument,
        timeframe="1m",
        from_time=candles[0].open_time,
        to_time=candles[-1].close_time,
        initial_equity=Decimal(10000),
    )

    assert result == [fake_run]
    assert calls[0]["strategy_version_id"] == strategy_version.id
    assert calls[0]["risk_profile_version_id"] == risk_profile_version.id
    db.commit.assert_called_once()


def test_run_backtest_for_workspace_walk_forward_mode_calls_run_and_persist_walk_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    instrument = _instrument()
    candles = [_candle(instrument.id, i) for i in range(5)]
    db.scalars.return_value.all.return_value = candles
    db.scalar.side_effect = ["BTCUSDT", DatasetSnapshot(id=uuid4())]

    from app.models.strategy import RiskProfileVersion, StrategyVersion

    strategy_version = StrategyVersion(id=uuid4(), strategy_id=uuid4(), version=1)
    risk_profile_version = RiskProfileVersion(id=uuid4(), risk_profile_id=uuid4(), version=1)
    monkeypatch.setattr(
        provisioning,
        "ensure_dummy_strategy_and_risk_profile",
        lambda db_, workspace_id: (strategy_version, risk_profile_version),
    )
    train_run = BacktestRun(id=uuid4(), workspace_id=uuid4())
    test_run = BacktestRun(id=uuid4(), workspace_id=uuid4())
    monkeypatch.setattr(
        provisioning,
        "run_and_persist_walk_forward",
        lambda db_, candles_, **kwargs: (train_run, test_run),
    )

    result = provisioning.run_backtest_for_workspace(
        db,
        uuid4(),
        instrument,
        timeframe="1m",
        from_time=candles[0].open_time,
        to_time=candles[-1].close_time,
        initial_equity=Decimal(10000),
        walk_forward=True,
    )

    assert result == [train_run, test_run]
    db.commit.assert_called_once()
