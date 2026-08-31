from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.market_data.application import use_cases as market_data_application
from app.models.catalog import (
    BackfillJob,
    Candle,
    MarketDataSubscription,
)
from app.schemas.catalog import (
    BackfillJobRead,
    CandleBackfillCreate,
    CandleCoverageRead,
    CandleRead,
    MarketDataCollectionUpdate,
    MarketDataSubscriptionRead,
    MarketDataSubscriptionUpdate,
    Timeframe,
)
from app.security.auth import require_owner
from app.services.market_data import (
    CandleIngestionService,
    MarketDataAccessError,
    run_backfill_job,
)
from app.services.secrets import get_secret_store

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Owner = Annotated[str, Depends(require_owner)]


def _validate_collection_configuration(
    db: Session, workspace_id: UUID, instrument_id: UUID
) -> None:
    try:
        CandleIngestionService(db, get_secret_store()).validate_configuration(
            workspace_id, instrument_id
        )
    except MarketDataAccessError as exc:
        raise HTTPException(
            status_code=409,
            detail="保存済み資格情報を読み込めません。接続管理でAPI資格情報を更新して再検証してください。",
        ) from exc


@contextmanager
def _application_errors() -> Iterator[None]:
    try:
        yield
    except market_data_application.MarketDataApplicationError as exc:
        status_code = {
            "workspace_not_found": 404,
            "instrument_unavailable": 409,
            "overlapping_backfill": 409,
            "invalid_input": 422,
        }[exc.code]
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def _require_workspace(db: Session, workspace_id: UUID) -> None:
    with _application_errors():
        market_data_application._require_workspace(db, workspace_id)


def _require_instrument_access(db: Session, workspace_id: UUID, instrument_id: UUID) -> None:
    with _application_errors():
        market_data_application._require_instrument_access(db, workspace_id, instrument_id)


@router.post(
    "/workspaces/{workspace_id}/candle-backfills",
    response_model=BackfillJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["market-data"],
)
def create_candle_backfill(
    workspace_id: UUID,
    payload: CandleBackfillCreate,
    background_tasks: BackgroundTasks,
    db: DatabaseSession,
    _: Owner,
) -> BackfillJob:
    with _application_errors():
        job = market_data_application.enqueue_backfill(
            db,
            workspace_id,
            market_data_application.BackfillCommand(
                payload.instrument_id, payload.timeframe, payload.days
            ),
            _validate_collection_configuration,
        )
    background_tasks.add_task(run_backfill_job, job.id)
    return job


@router.get(
    "/workspaces/{workspace_id}/candle-backfills",
    response_model=list[BackfillJobRead],
    tags=["market-data"],
)
def list_candle_backfills(
    workspace_id: UUID,
    db: DatabaseSession,
    _: Owner,
    instrument_id: UUID | None = None,
    timeframe: Timeframe | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[BackfillJob]:
    _require_workspace(db, workspace_id)
    statement = select(BackfillJob).where(BackfillJob.workspace_id == workspace_id)
    if instrument_id is not None:
        statement = statement.where(BackfillJob.instrument_id == instrument_id)
    if timeframe is not None:
        statement = statement.where(BackfillJob.timeframe == timeframe)
    return list(db.scalars(statement.order_by(BackfillJob.created_at.desc()).limit(limit)).all())


@router.get(
    "/workspaces/{workspace_id}/instruments/{instrument_id}/candles",
    response_model=list[CandleRead],
    tags=["market-data"],
)
def list_candles(
    workspace_id: UUID,
    instrument_id: UUID,
    db: DatabaseSession,
    _: Owner,
    timeframe: Timeframe = "1m",
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    before: datetime | None = None,
) -> list[CandleRead]:
    _require_workspace(db, workspace_id)
    _require_instrument_access(db, workspace_id, instrument_id)
    if before is not None and before.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The before cursor must include a timezone offset",
        )
    statement = select(Candle).where(
        Candle.instrument_id == instrument_id,
        Candle.timeframe == timeframe,
        Candle.is_final.is_(True),
    )
    if before is not None:
        statement = statement.where(Candle.open_time < before.astimezone(UTC))
    candles = list(
        db.scalars(statement.order_by(Candle.open_time.desc(), Candle.id.desc()).limit(limit)).all()
    )
    return [_candle_read(candle) for candle in reversed(candles)]


@router.get(
    "/workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage",
    response_model=CandleCoverageRead,
    tags=["market-data"],
)
def get_candle_coverage(
    workspace_id: UUID,
    instrument_id: UUID,
    db: DatabaseSession,
    _: Owner,
    timeframe: Timeframe = "1m",
    requested_from: datetime | None = None,
    requested_to: datetime | None = None,
) -> CandleCoverageRead:
    with _application_errors():
        report = market_data_application.get_coverage(
            db, workspace_id, instrument_id, timeframe, requested_from, requested_to
        )
    return CandleCoverageRead.model_validate({"timeframe": timeframe, **report})


@router.put(
    "/workspaces/{workspace_id}/market-data-subscription",
    response_model=MarketDataSubscriptionRead,
    tags=["market-data"],
)
def update_market_data_subscription(
    workspace_id: UUID,
    payload: MarketDataSubscriptionUpdate,
    db: DatabaseSession,
    _: Owner,
) -> MarketDataSubscription:
    with _application_errors():
        return market_data_application.update_subscriptions(
            db,
            workspace_id,
            payload.instrument_id,
            payload.enabled,
            _validate_collection_configuration,
            timeframe=payload.timeframe,
        )[0]


@router.put(
    "/workspaces/{workspace_id}/market-data-subscriptions",
    response_model=list[MarketDataSubscriptionRead],
    tags=["market-data"],
)
def update_all_market_data_subscriptions(
    workspace_id: UUID,
    payload: MarketDataCollectionUpdate,
    db: DatabaseSession,
    _: Owner,
) -> list[MarketDataSubscription]:
    with _application_errors():
        return market_data_application.update_subscriptions(
            db,
            workspace_id,
            payload.instrument_id,
            payload.enabled,
            _validate_collection_configuration,
        )


@router.get(
    "/workspaces/{workspace_id}/market-data-subscriptions",
    response_model=list[MarketDataSubscriptionRead],
    tags=["market-data"],
)
def list_market_data_subscriptions(
    workspace_id: UUID, db: DatabaseSession, _: Owner
) -> list[MarketDataSubscription]:
    _require_workspace(db, workspace_id)
    return list(
        db.scalars(
            select(MarketDataSubscription)
            .where(MarketDataSubscription.workspace_id == workspace_id)
            .order_by(MarketDataSubscription.created_at.desc())
        ).all()
    )


def _candle_read(candle: Candle) -> CandleRead:
    return CandleRead(
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=_decimal_text(candle.open),
        high=_decimal_text(candle.high),
        low=_decimal_text(candle.low),
        close=_decimal_text(candle.close),
        volume=_decimal_text(candle.volume) if candle.volume is not None else None,
        trade_count=candle.trade_count,
        source=candle.source,
        quality_status=candle.quality_status,
        is_final=candle.is_final,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
