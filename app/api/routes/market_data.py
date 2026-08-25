from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.catalog import (
    AuditLog,
    BackfillJob,
    Candle,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    MarketDataSubscription,
    Workspace,
    WorkspaceAccountSelection,
)
from app.schemas.catalog import (
    BackfillJobRead,
    CandleBackfillCreate,
    CandleCoverageRead,
    CandleRead,
    MarketDataSubscriptionRead,
    MarketDataSubscriptionUpdate,
    Timeframe,
)
from app.security.auth import require_owner
from app.services.market_data import build_candle_coverage, run_backfill_job

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
Owner = Annotated[str, Depends(require_owner)]


def _require_workspace(db: Session, workspace_id: UUID) -> None:
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


def _require_instrument_access(db: Session, workspace_id: UUID, instrument_id: UUID) -> None:
    accessible = db.scalar(
        select(Instrument.id)
        .join(Exchange, Instrument.exchange_id == Exchange.id)
        .join(
            WorkspaceAccountSelection,
            (WorkspaceAccountSelection.workspace_id == workspace_id)
            & (WorkspaceAccountSelection.exchange_id == Exchange.id),
        )
        .join(
            ExternalAccount,
            ExternalAccount.id == WorkspaceAccountSelection.external_account_id,
        )
        .join(ExchangeConnection, ExchangeConnection.id == ExternalAccount.connection_id)
        .where(
            Instrument.id == instrument_id,
            Instrument.status == "active",
            ExternalAccount.status == "active",
            ExchangeConnection.workspace_id == workspace_id,
            ExchangeConnection.status == "verified",
        )
    )
    if accessible is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select an active account with a verified connection for this instrument",
        )


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
    _require_workspace(db, workspace_id)
    _require_instrument_access(db, workspace_id, payload.instrument_id)
    now = datetime.now(UTC)
    job = BackfillJob(
        workspace_id=workspace_id,
        instrument_id=payload.instrument_id,
        timeframe=payload.timeframe,
        from_time=now - timedelta(days=payload.days),
        to_time=now,
        requested_by=None,
        trigger_type="manual",
        status="queued",
    )
    db.add(job)
    db.flush()
    _audit(
        db,
        workspace_id,
        "candle.backfill_queued",
        "backfill_job",
        job.id,
        {
            "instrument_id": str(payload.instrument_id),
            "timeframe": payload.timeframe,
            "days": payload.days,
        },
    )
    db.commit()
    db.refresh(job)
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
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[BackfillJob]:
    _require_workspace(db, workspace_id)
    statement = select(BackfillJob).where(BackfillJob.workspace_id == workspace_id)
    if instrument_id is not None:
        statement = statement.where(BackfillJob.instrument_id == instrument_id)
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
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[CandleRead]:
    _require_workspace(db, workspace_id)
    _require_instrument_access(db, workspace_id, instrument_id)
    candles = list(
        db.scalars(
            select(Candle)
            .where(
                Candle.instrument_id == instrument_id,
                Candle.timeframe == timeframe,
                Candle.is_final.is_(True),
            )
            .order_by(Candle.open_time.desc())
            .limit(limit)
        ).all()
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
) -> CandleCoverageRead:
    _require_workspace(db, workspace_id)
    _require_instrument_access(db, workspace_id, instrument_id)
    latest_job = db.scalar(
        select(BackfillJob)
        .where(
            BackfillJob.workspace_id == workspace_id,
            BackfillJob.instrument_id == instrument_id,
            BackfillJob.timeframe == timeframe,
        )
        .order_by(BackfillJob.created_at.desc())
        .limit(1)
    )
    report = build_candle_coverage(
        db,
        instrument_id,
        timeframe,
        latest_job.from_time if latest_job else None,
        latest_job.to_time if latest_job else None,
    )
    return CandleCoverageRead(timeframe=timeframe, **report)


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
    _require_workspace(db, workspace_id)
    _require_instrument_access(db, workspace_id, payload.instrument_id)
    subscription = db.scalar(
        select(MarketDataSubscription).where(
            MarketDataSubscription.workspace_id == workspace_id,
            MarketDataSubscription.instrument_id == payload.instrument_id,
            MarketDataSubscription.timeframe == payload.timeframe,
        )
    )
    if subscription is None:
        subscription = MarketDataSubscription(
            workspace_id=workspace_id,
            instrument_id=payload.instrument_id,
            timeframe=payload.timeframe,
        )
        db.add(subscription)
        db.flush()
    before = {"enabled": subscription.enabled}
    subscription.enabled = payload.enabled
    subscription.poll_interval_seconds = 60
    subscription.updated_at = datetime.now(UTC)
    _audit(
        db,
        workspace_id,
        "market_data.subscription_updated",
        "market_data_subscription",
        subscription.id,
        {
            "instrument_id": str(payload.instrument_id),
            "timeframe": payload.timeframe,
            "enabled": payload.enabled,
            "poll_interval_seconds": 60,
            "before": before,
        },
    )
    db.commit()
    db.refresh(subscription)
    return subscription


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


def _audit(
    db: Session,
    workspace_id: UUID,
    action: str,
    resource_type: str,
    resource_id: UUID,
    after_data: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_data=None,
            after_data=after_data,
            correlation_id=uuid4(),
            ip_address=None,
            user_agent=None,
        )
    )
