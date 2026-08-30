"""HTTP-independent market-data commands and transaction boundaries."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.catalog import (
    AuditLog,
    BackfillJob,
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    Instrument,
    MarketDataSubscription,
    Workspace,
    WorkspaceAccountSelection,
)
from app.services.market_data import (
    DuplicateBackfillError,
    _advisory_lock_key,
    build_candle_coverage,
    ensure_no_overlapping_backfill,
)

SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
ConfigurationValidator = Callable[[Session, UUID, UUID], None]


class MarketDataApplicationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BackfillCommand:
    instrument_id: UUID
    timeframe: str
    days: int


@dataclass(frozen=True)
class SubscriptionCommand:
    instrument_id: UUID
    timeframe: str
    enabled: bool


@contextmanager
def _rollback_on_failure(db: Session) -> Iterator[None]:
    try:
        yield
    except Exception:
        db.rollback()
        raise


def _validate_timeframe(timeframe: str) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise MarketDataApplicationError("invalid_input", "Unsupported timeframe")


def _lock_collection(db: Session, workspace_id: UUID, instrument_id: UUID) -> None:
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                _advisory_lock_key("collection", workspace_id, instrument_id)
            )
        )
    )


def _require_workspace(db: Session, workspace_id: UUID) -> None:
    if db.get(Workspace, workspace_id) is None:
        raise MarketDataApplicationError("workspace_not_found", "Workspace not found")


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
        raise MarketDataApplicationError(
            "instrument_unavailable",
            "Select an active account with a verified connection for this instrument",
        )


def enqueue_backfill(
    db: Session,
    workspace_id: UUID,
    payload: BackfillCommand,
    validate_configuration: ConfigurationValidator,
) -> BackfillJob:
    with _rollback_on_failure(db):
        _validate_timeframe(payload.timeframe)
        if not 1 <= payload.days <= 365:
            raise MarketDataApplicationError("invalid_input", "days must be between 1 and 365")
        _require_workspace(db, workspace_id)
        _require_instrument_access(db, workspace_id, payload.instrument_id)
        now = datetime.now(UTC)
        requested_from = now - timedelta(days=payload.days)
        try:
            ensure_no_overlapping_backfill(
                db,
                workspace_id=workspace_id,
                instrument_id=payload.instrument_id,
                timeframe=payload.timeframe,
                requested_from=requested_from,
                requested_to=now,
            )
        except DuplicateBackfillError as exc:
            raise MarketDataApplicationError("overlapping_backfill", str(exc)) from exc
        validate_configuration(db, workspace_id, payload.instrument_id)
        job = BackfillJob(
            workspace_id=workspace_id,
            instrument_id=payload.instrument_id,
            timeframe=payload.timeframe,
            from_time=requested_from,
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
        return job


def get_coverage(
    db: Session,
    workspace_id: UUID,
    instrument_id: UUID,
    timeframe: str,
    requested_from: datetime | None = None,
    requested_to: datetime | None = None,
) -> dict[str, object]:
    _validate_timeframe(timeframe)
    _require_workspace(db, workspace_id)
    _require_instrument_access(db, workspace_id, instrument_id)
    if (requested_from is None) != (requested_to is None):
        raise MarketDataApplicationError(
            "invalid_input", "requested_from and requested_to must be provided together"
        )
    if requested_from is not None and requested_to is not None:
        if requested_from.tzinfo is None or requested_to.tzinfo is None:
            raise MarketDataApplicationError(
                "invalid_input", "Coverage range timestamps must include a timezone offset"
            )
        requested_from = requested_from.astimezone(UTC)
        requested_to = requested_to.astimezone(UTC)
        if requested_from >= requested_to:
            raise MarketDataApplicationError(
                "invalid_input", "requested_from must be earlier than requested_to"
            )
    else:
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
        requested_from = latest_job.from_time if latest_job else None
        requested_to = latest_job.to_time if latest_job else None
    report = build_candle_coverage(
        db,
        instrument_id,
        timeframe,
        requested_from,
        requested_to,
    )
    return report


def update_subscriptions(
    db: Session,
    workspace_id: UUID,
    instrument_id: UUID,
    enabled: bool,
    validate_configuration: ConfigurationValidator,
    timeframe: str | None = None,
) -> list[MarketDataSubscription]:
    with _rollback_on_failure(db):
        frames = SUPPORTED_TIMEFRAMES if timeframe is None else (timeframe,)
        for frame in frames:
            _validate_timeframe(frame)
        _require_workspace(db, workspace_id)
        _require_instrument_access(db, workspace_id, instrument_id)
        if enabled:
            validate_configuration(db, workspace_id, instrument_id)
        _lock_collection(db, workspace_id, instrument_id)
        subscriptions = [
            _set_subscription(db, workspace_id, SubscriptionCommand(instrument_id, frame, enabled))
            for frame in frames
        ]
        db.commit()
        for subscription in subscriptions:
            db.refresh(subscription)
        return subscriptions


def _set_subscription(
    db: Session, workspace_id: UUID, payload: SubscriptionCommand
) -> MarketDataSubscription:
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
    return subscription


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
