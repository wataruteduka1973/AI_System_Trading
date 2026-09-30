"""Provisions the inputs a backtest needs (candle range, `DatasetSnapshot`,
`StrategyVersion`/`RiskProfileVersion`) and ties Units 1-6 of
docs/plans/horizon4-lite-backtest.md into one workspace-facing entry point
(Horizon 4 API/UI task, 2026-09-26 -- same "already fully built, zero HTTP
callers" state Horizon 3's Bot management API found `dummy_pipeline.py` in).

`docs/plans/horizon4-lite-backtest.md`'s own dataflow step 2
("dataset_snapshotを作成: 対象期間のcandle件数・欠損チェック・checksum算出") was
the one piece of Units 1-6 that had never actually been implemented -- every
`DatasetSnapshot` in this codebase before this module existed was constructed by
hand in `app/models/backtest.py`'s own tests. `ensure_dataset_snapshot` below is
that missing piece.
"""

import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun, DatasetSnapshot
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import Candle, MarketDataGap
from app.trading.application.backtest_metrics import run_and_persist_backtest
from app.trading.application.backtest_walk_forward import run_and_persist_walk_forward
from app.trading.application.dummy_pipeline import ensure_dummy_strategy_and_risk_profile
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES


class BacktestProvisioningError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _checksum(payload: object) -> str:
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _exchange_code_for_instrument(db: Session, instrument: Instrument) -> str:
    """`run_replay`/`backtest_fill.py` key their fee/slippage/short-selling rules
    off `exchange_code` (only "oanda"/"binance" are recognized), so a
    `binance_public` research instrument (2026-09-26, real production Binance
    klines fetched for backtesting -- see `app/exchanges/binance_public.py`)
    is normalized to "binance" here: it is mechanically the same spot market,
    just sourced from public production data instead of a credentialed
    Testnet connection. This function's own return value is otherwise only
    used to pick a fee/mechanics *model*, not to identify the literal
    `Exchange` catalog row."""
    code = db.scalar(select(Exchange.code).where(Exchange.id == instrument.exchange_id))
    if code is None:
        raise BacktestProvisioningError(
            "exchange_not_found", "Could not resolve exchange code for this instrument"
        )
    return "binance" if code == "binance_public" else code


def load_final_candles(
    db: Session, instrument_id: UUID, timeframe: str, from_time: datetime, to_time: datetime
) -> list[Candle]:
    """Every final candle in `[from_time, to_time)`, ascending by `open_time` --
    unlike `market_data.py`'s live-viewing endpoint, no 500-row cap: a backtest
    genuinely needs its entire requested range in one pass."""
    return list(
        db.scalars(
            select(Candle)
            .where(
                Candle.instrument_id == instrument_id,
                Candle.timeframe == timeframe,
                Candle.is_final.is_(True),
                Candle.open_time >= from_time,
                Candle.open_time < to_time,
            )
            .order_by(Candle.open_time)
        ).all()
    )


def ensure_dataset_snapshot(
    db: Session,
    workspace_id: UUID,
    instrument: Instrument,
    *,
    timeframe: str,
    from_time: datetime,
    to_time: datetime,
    candles: list[Candle],
) -> DatasetSnapshot:
    """Idempotent by `(workspace_id, checksum)` (`uq_dataset_snapshot_checksum`):
    re-running a backtest over the exact same instrument/timeframe/range/candle set
    reuses the prior snapshot instead of accumulating duplicates. `quality_report`
    summarizes `market_data_gap` rows overlapping the range -- the documented
    Horizon4-lite substitute for a full ML-pipeline quality report (see
    docs/plans/horizon4-lite-backtest.md point 1: this table's other columns are
    designed for a future feature-engineering pipeline this Unit does not build).
    `storage_uri` is a reference into `fx.candle`, not an export -- no copy of the
    candle data is made anywhere else."""
    checksum = _checksum(
        {
            "instrument_id": str(instrument.id),
            "timeframe": timeframe,
            "from_time": from_time.isoformat(),
            "to_time": to_time.isoformat(),
            "candle_ids": sorted(str(candle.id) for candle in candles),
        }
    )
    existing = db.scalar(
        select(DatasetSnapshot).where(
            DatasetSnapshot.workspace_id == workspace_id, DatasetSnapshot.checksum == checksum
        )
    )
    if existing is not None:
        return existing

    gaps = db.scalars(
        select(MarketDataGap).where(
            MarketDataGap.instrument_id == instrument.id,
            MarketDataGap.timeframe == timeframe,
            MarketDataGap.from_time < to_time,
            MarketDataGap.to_time > from_time,
        )
    ).all()
    quality_report = {
        "gap_count": len(gaps),
        "open_gap_count": sum(1 for gap in gaps if gap.status == "open"),
        "total_missing_count": sum(gap.missing_count or 0 for gap in gaps),
    }

    snapshot = DatasetSnapshot(
        workspace_id=workspace_id,
        instruments={"instrument_id": str(instrument.id), "symbol": instrument.symbol},
        intervals={"timeframe": timeframe},
        from_time=from_time,
        to_time=to_time,
        feature_schema={"columns": ["open_time", "close_time", "open", "high", "low", "close"]},
        row_count=len(candles),
        quality_report=quality_report,
        checksum=checksum,
        storage_uri=(
            f"db://fx.candle?instrument_id={instrument.id}&timeframe={timeframe}"
            f"&from={from_time.isoformat()}&to={to_time.isoformat()}"
        ),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def run_backtest_for_workspace(
    db: Session,
    workspace_id: UUID,
    instrument: Instrument,
    *,
    timeframe: str,
    from_time: datetime,
    to_time: datetime,
    initial_equity: Decimal,
    spread: Decimal = Decimal(0),
    walk_forward: bool = False,
    train_ratio: Decimal = Decimal("0.7"),
) -> list[BacktestRun]:
    """The one entry point the API route calls: resolves the workspace's shared
    dummy `StrategyVersion`/`RiskProfileVersion` (same one `ensure_dummy_bot` uses
    -- there is only one real strategy implementation in this codebase), loads the
    requested candle range, provisions a `DatasetSnapshot`, and runs+persists either
    a single backtest or a walk-forward train/test pair. Returns one `BacktestRun`
    for `walk_forward=False`, two (`[train, test]`) for `walk_forward=True`. Runs
    synchronously in this call -- there is no job queue in this codebase, matching
    every other pipeline entry point here (`order_flow.py`, `dummy_pipeline.py`).
    One transaction: nothing is committed until the run(s) are fully persisted."""
    candles = load_final_candles(db, instrument.id, timeframe, from_time, to_time)
    if not candles:
        raise BacktestProvisioningError(
            "no_candles", "No final candles are available for this instrument/timeframe/range"
        )

    strategy_version, risk_profile_version = ensure_dummy_strategy_and_risk_profile(
        db, workspace_id
    )
    exchange_code = _exchange_code_for_instrument(db, instrument)
    dataset_snapshot = ensure_dataset_snapshot(
        db,
        workspace_id,
        instrument,
        timeframe=timeframe,
        from_time=from_time,
        to_time=to_time,
        candles=candles,
    )

    if walk_forward:
        train_run, test_run = run_and_persist_walk_forward(
            db,
            candles,
            workspace_id=workspace_id,
            strategy_version_id=strategy_version.id,
            risk_profile_version_id=risk_profile_version.id,
            dataset_snapshot_id=dataset_snapshot.id,
            instrument=instrument,
            timeframe=timeframe,
            exchange_code=exchange_code,
            rules=CONSERVATIVE_V1_RULES,
            initial_equity=initial_equity,
            code_version="dummy-pipeline-0.1",
            spread=spread,
            train_ratio=train_ratio,
        )
        db.commit()
        return [train_run, test_run]

    run = run_and_persist_backtest(
        db,
        candles,
        workspace_id=workspace_id,
        strategy_version_id=strategy_version.id,
        risk_profile_version_id=risk_profile_version.id,
        dataset_snapshot_id=dataset_snapshot.id,
        instrument=instrument,
        timeframe=timeframe,
        exchange_code=exchange_code,
        rules=CONSERVATIVE_V1_RULES,
        initial_equity=initial_equity,
        code_version="dummy-pipeline-0.1",
        spread=spread,
    )
    db.commit()
    return [run]
