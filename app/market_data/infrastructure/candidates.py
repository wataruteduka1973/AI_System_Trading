"""Read-only discovery of due work. Never locks rows; claim() re-validates due-ness."""

from datetime import datetime
from heapq import merge
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.market_data.infrastructure.leases import ACTIVE_JOBS, FeedKey, WorkKind, WorkRef
from app.market_data.infrastructure.models import WorkerBackfill, WorkerSubscription

_Row = tuple[datetime, UUID, UUID, UUID, str]


class CandidateScanner:
    def __init__(self, sessions: sessionmaker) -> None:
        self.sessions = sessions

    def due(self, limit: int) -> list[WorkRef]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        with self.sessions() as db:
            now = db.execute(select(func.clock_timestamp())).scalar_one()
            backfills: list[_Row] = [
                (next_run_at, work_id, workspace_id, instrument_id, timeframe)
                for workspace_id, instrument_id, timeframe, work_id, next_run_at in db.execute(
                    select(
                        WorkerBackfill.workspace_id,
                        WorkerBackfill.instrument_id,
                        WorkerBackfill.timeframe,
                        WorkerBackfill.id,
                        WorkerBackfill.next_run_at,
                    )
                    .where(
                        WorkerBackfill.status.in_(ACTIVE_JOBS), WorkerBackfill.next_run_at <= now
                    )
                    .order_by(WorkerBackfill.next_run_at, WorkerBackfill.id)
                    .limit(limit)
                ).all()
            ]
            subscriptions: list[_Row] = [
                (next_run_at, work_id, workspace_id, instrument_id, timeframe)
                for workspace_id, instrument_id, timeframe, work_id, next_run_at in db.execute(
                    select(
                        WorkerSubscription.workspace_id,
                        WorkerSubscription.instrument_id,
                        WorkerSubscription.timeframe,
                        WorkerSubscription.id,
                        WorkerSubscription.next_run_at,
                    )
                    .where(
                        WorkerSubscription.enabled.is_(True),
                        WorkerSubscription.blocked_reason.is_(None),
                        WorkerSubscription.next_run_at <= now,
                    )
                    .order_by(WorkerSubscription.next_run_at, WorkerSubscription.id)
                    .limit(limit)
                ).all()
            ]

        tagged_backfills: list[tuple[_Row, WorkKind]] = [(row, "backfill") for row in backfills]
        tagged_subscriptions: list[tuple[_Row, WorkKind]] = [
            (row, "polling") for row in subscriptions
        ]
        ordered = merge(tagged_backfills, tagged_subscriptions, key=lambda item: item[0])
        result = [
            WorkRef(FeedKey(workspace_id, instrument_id, timeframe), kind, work_id)
            for (_, work_id, workspace_id, instrument_id, timeframe), kind in ordered
        ]
        return result[:limit]
