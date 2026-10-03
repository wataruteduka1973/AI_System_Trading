"""Serializes backfill requests per (workspace, instrument, timeframe) with a
transaction-scoped PostgreSQL advisory lock, and refuses one that overlaps a
queued or running job (docs/plans/market-data-services-consolidation.md).
`advisory_lock_key` is also what `use_cases.update_subscriptions` and
`candle_store.persist_internal_gaps` lock on."""

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market_data import BackfillJob


class DuplicateBackfillError(RuntimeError):
    pass


def advisory_lock_key(namespace: str, *parts: object) -> int:
    lock_material = ":".join((namespace, *(str(part) for part in parts))).encode()
    return int.from_bytes(hashlib.blake2b(lock_material, digest_size=8).digest(), signed=True)


def ensure_no_overlapping_backfill(
    db: Session,
    *,
    workspace_id: UUID,
    instrument_id: UUID,
    timeframe: str,
    requested_from: datetime,
    requested_to: datetime,
) -> None:
    lock_key = advisory_lock_key("backfill", workspace_id, instrument_id, timeframe)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    duplicate_id = db.scalar(
        select(BackfillJob.id).where(
            BackfillJob.workspace_id == workspace_id,
            BackfillJob.instrument_id == instrument_id,
            BackfillJob.timeframe == timeframe,
            BackfillJob.status.in_(("queued", "running")),
            BackfillJob.from_time < requested_to,
            BackfillJob.to_time > requested_from,
        )
    )
    if duplicate_id is not None:
        raise DuplicateBackfillError("An overlapping backfill is already queued or running")
