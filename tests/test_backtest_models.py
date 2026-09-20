"""Unit 1 (docs/plans/horizon4-lite-backtest.md): ORM mapping for
DatasetSnapshot/BacktestRun/BacktestTrade, plus the two `backtest_runs`
relationships added to StrategyVersion/RiskProfileVersion. No DB connection is
used, matching this codebase's existing model tests (e.g. test_model_metadata.py):
`configure_mappers()` resolves every ForeignKey string against the shared
`Base.metadata`, which is enough to catch an unresolved target without a live
database.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.db.session import Base
from app.models import BacktestRun, BacktestTrade, DatasetSnapshot
from sqlalchemy.orm import configure_mappers


def test_backtest_tables_are_registered() -> None:
    configure_mappers()

    assert DatasetSnapshot.__table__ is Base.metadata.tables["fx.dataset_snapshot"]
    assert BacktestRun.__table__ is Base.metadata.tables["fx.backtest_run"]
    assert BacktestTrade.__table__ is Base.metadata.tables["fx.backtest_trade"]
    assert BacktestRun.__table__.c.dataset_snapshot_id.foreign_keys
    assert BacktestRun.__table__.c.strategy_version_id.foreign_keys
    assert BacktestRun.__table__.c.risk_profile_version_id.foreign_keys
    assert BacktestTrade.__table__.c.backtest_run_id.foreign_keys
    assert BacktestTrade.__table__.c.instrument_id.foreign_keys


def test_backtest_trade_metadata_column_uses_reserved_name_workaround() -> None:
    # The DB column is literally named "metadata"; the Python attribute is
    # `metadata_` so it does not shadow `Base.metadata` (the MetaData registry).
    assert "metadata" in BacktestTrade.__table__.columns
    assert BacktestTrade.metadata is Base.metadata
    trade = BacktestTrade(metadata_={"note": "x"})
    assert trade.metadata_ == {"note": "x"}


def _dataset_snapshot(**overrides: object) -> DatasetSnapshot:
    now = datetime.now(UTC)
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        instruments={"instrument_ids": [str(uuid4())]},
        intervals={"timeframe": "1m"},
        from_time=now,
        to_time=now,
        feature_schema={"columns": ["open", "high", "low", "close"]},
        row_count=0,
        quality_report={},
        checksum="a" * 64,
        storage_uri="db://fx.candle",
    )
    defaults.update(overrides)
    return DatasetSnapshot(**defaults)


def _backtest_run(dataset_snapshot: DatasetSnapshot, **overrides: object) -> BacktestRun:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        dataset_snapshot_id=dataset_snapshot.id,
        parameters={},
        code_version="test",
        status="queued",
    )
    defaults.update(overrides)
    run = BacktestRun(**defaults)
    run.dataset_snapshot = dataset_snapshot
    return run


def test_backtest_run_relates_to_its_dataset_snapshot_both_ways() -> None:
    snapshot = _dataset_snapshot()
    run = _backtest_run(snapshot)

    assert run.dataset_snapshot is snapshot
    assert run in snapshot.backtest_runs


def test_backtest_trade_relates_to_its_backtest_run_both_ways() -> None:
    run = _backtest_run(_dataset_snapshot())
    trade = BacktestTrade(
        id=uuid4(),
        backtest_run_id=run.id,
        sequence_no=1,
        instrument_id=uuid4(),
        side="buy",
        entry_time=datetime.now(UTC),
        entry_price=Decimal("100"),
        quantity=Decimal("1"),
    )

    trade.backtest_run = run

    assert trade.backtest_run is run
    assert trade in run.trades
