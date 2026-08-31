"""Short, fenced PostgreSQL transactions. Not connected to the legacy poller.

The caller must validate exchange access in the Application layer before fetching
and inside guarded_write before persisting candles. No network I/O belongs here.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.market_data.infrastructure.models import (
    MarketDataLease,
    WorkerBackfill,
    WorkerSubscription,
)
from app.models.catalog import AuditLog

WorkKind = Literal["backfill", "polling"]
WorkRow = WorkerBackfill | WorkerSubscription
ACTIVE_JOBS = ("queued", "running", "validating")


class LeaseLost(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Market-data work ownership is no longer valid")


@dataclass(frozen=True)
class FeedKey:
    workspace_id: UUID
    instrument_id: UUID
    timeframe: str


@dataclass(frozen=True)
class WorkRef:
    feed: FeedKey
    kind: WorkKind
    id: UUID

    def __post_init__(self) -> None:
        if self.kind not in ("backfill", "polling"):
            raise ValueError("Unsupported work kind")


@dataclass(frozen=True)
class LeaseClaim:
    work: WorkRef
    owner_id: UUID
    token: UUID
    next_fetch_at: datetime | None


class LeaseStore:
    def __init__(self, sessions: sessionmaker[Session], lease_seconds: int = 90) -> None:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        if AuditLog.__table__.schema != "fx":
            raise ValueError("Worker storage currently requires the fx schema")
        self.sessions = sessions
        self.duration = timedelta(seconds=lease_seconds)

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self.sessions.begin() as db:
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            db.execute(text("SET LOCAL idle_in_transaction_session_timeout = '10s'"))
            yield db

    def claim(self, work: WorkRef, owner_id: UUID) -> LeaseClaim | None:
        with self._transaction() as db:
            # A try-lock also prevents concurrent creation of the very first feed row
            # from waiting on an uncommitted unique-key insertion.
            key = int.from_bytes(
                hashlib.blake2b(f"market-data-lease:{work.feed}".encode(), digest_size=8).digest(),
                "big",
                signed=True,
            )
            if not db.scalar(select(func.pg_try_advisory_xact_lock(key))):
                return None
            feed = work.feed
            identity = (feed.workspace_id, feed.instrument_id, feed.timeframe)
            if db.get(MarketDataLease, identity) is None:
                db.add(
                    MarketDataLease(
                        workspace_id=feed.workspace_id,
                        instrument_id=feed.instrument_id,
                        timeframe=feed.timeframe,
                    )
                )
                db.flush()
            lease = self._lock(db, feed, skip_locked=True)
            if lease is None:
                return None
            now = self._now(db)
            if lease.lease_token is not None:
                if lease.lease_until is None or lease.lease_until > now:
                    return None
                self._recover(db, lease, now)
                db.flush()
            target = self._target(db, work)
            if target is None or not self._active(target) or target.next_run_at > now:
                return None
            before = self._state(target)
            token = uuid4()
            lease.owner_id, lease.lease_token = owner_id, token
            lease.heartbeat_at, lease.lease_until = now, now + self.duration
            lease.work_kind, lease.work_id = work.kind, work.id
            if isinstance(target, WorkerBackfill) and target.status == "queued":
                target.status = "running"
                target.attempts += 1
                if target.started_at is None:
                    target.started_at = now
                target.finished_at = None
            self._audit(
                db,
                work,
                "market_data.work_claimed",
                {"kind": work.kind, **self._state(target)},
                before,
            )
            return LeaseClaim(work, owner_id, token, target.next_fetch_at)

    def heartbeat(self, claim: LeaseClaim) -> bool:
        with self._transaction() as db:
            lease = self._lock(db, claim.work.feed)
            now = self._now(db)
            if lease is None or not self._owned(lease, claim, now):
                return False
            lease.heartbeat_at, lease.lease_until = now, now + self.duration
            return True

    def release(self, claim: LeaseClaim) -> bool:
        """Graceful release: no retry/failure increment and no cursor mutation."""
        with self._transaction() as db:
            lease = self._lock(db, claim.work.feed)
            if lease is None or not self._owned(lease, claim, self._now(db)):
                return False
            self._clear(lease)
            return True

    @contextmanager
    def guarded_write(
        self, claim: LeaseClaim, *, release: bool = True
    ) -> Iterator[tuple[Session, WorkRow]]:
        """Yield only after ownership, target and cursor checks; commit atomically.

        Caller writes and target progress share this transaction. It must not
        commit/rollback it, do external I/O, or retain returned ORM entities.
        """
        with self._transaction() as db:
            lease = self._lock(db, claim.work.feed)
            if lease is None or not self._owned(lease, claim, self._now(db)):
                raise LeaseLost()
            target = self._target(db, claim.work)
            if (
                target is None
                or not self._active(target)
                or target.next_fetch_at != claim.next_fetch_at
            ):
                raise LeaseLost()
            yield db, target
            if release:
                self._clear(lease)

    def recover_expired(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._transaction() as db:
            leases = db.scalars(
                select(MarketDataLease)
                .where(MarketDataLease.lease_until <= func.clock_timestamp())
                .order_by(MarketDataLease.lease_until)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for lease in leases:
                self._recover(db, lease, self._now(db))
            return len(leases)

    def _recover(self, db: Session, lease: MarketDataLease, now: datetime) -> None:
        if lease.work_kind not in ("backfill", "polling") or lease.work_id is None:
            raise RuntimeError("Invalid stored lease target")
        kind: WorkKind = "backfill" if lease.work_kind == "backfill" else "polling"
        work = WorkRef(
            FeedKey(lease.workspace_id, lease.instrument_id, lease.timeframe),
            kind,
            lease.work_id,
        )
        target = self._target(db, work)
        before: dict[str, object] = (
            self._state(target) if target is not None else {"target_missing": True}
        )
        result: dict[str, object] = {"error_code": "worker_interrupted", "kind": work.kind}
        if target is not None and self._active(target):
            target.consecutive_failures += 1
            failures = target.consecutive_failures
            # Deterministic small jitter avoids tight synchronized recovery loops.
            delay = (30 if failures == 1 else 120) + work.id.int % 6
            target.next_run_at = now + timedelta(seconds=delay)
            if isinstance(target, WorkerBackfill):
                target.error_code = "worker_interrupted"
                target.status = "failed" if failures >= 3 else "queued"
                target.finished_at = now if failures >= 3 else None
                result["status"] = target.status
            else:
                target.last_error_code = "worker_interrupted"
                if failures >= 3:
                    target.blocked_reason = "worker_interrupted"
                result["blocked"] = target.blocked_reason is not None
            result["consecutive_failures"] = failures
        else:
            result["target_inactive"] = True
        self._audit(db, work, "market_data.lease_recovered", result, before)
        self._clear(lease)

    @staticmethod
    def _now(db: Session) -> datetime:
        return db.execute(select(func.clock_timestamp())).scalar_one()

    @staticmethod
    def _lock(db: Session, feed: FeedKey, skip_locked: bool = False) -> MarketDataLease | None:
        return db.scalar(
            select(MarketDataLease)
            .where(
                MarketDataLease.workspace_id == feed.workspace_id,
                MarketDataLease.instrument_id == feed.instrument_id,
                MarketDataLease.timeframe == feed.timeframe,
            )
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _target(db: Session, work: WorkRef) -> WorkRow | None:
        model: type[WorkerBackfill] | type[WorkerSubscription] = (
            WorkerBackfill if work.kind == "backfill" else WorkerSubscription
        )
        target = db.scalar(
            select(model)
            .where(
                model.id == work.id,
                model.workspace_id == work.feed.workspace_id,
                model.instrument_id == work.feed.instrument_id,
                model.timeframe == work.feed.timeframe,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is None or isinstance(target, (WorkerBackfill, WorkerSubscription)):
            return target
        raise RuntimeError("Unexpected worker mapping")

    @staticmethod
    def _active(target: WorkRow) -> bool:
        if isinstance(target, WorkerBackfill):
            return target.status in ACTIVE_JOBS
        return target.enabled and target.blocked_reason is None

    @staticmethod
    def _owned(lease: MarketDataLease | None, claim: LeaseClaim, now: datetime) -> bool:
        return (
            lease is not None
            and lease.lease_token == claim.token
            and lease.owner_id == claim.owner_id
            and lease.work_kind == claim.work.kind
            and lease.work_id == claim.work.id
            and lease.lease_until is not None
            and lease.lease_until > now
        )

    @staticmethod
    def _clear(lease: MarketDataLease) -> None:
        lease.owner_id = lease.lease_token = None
        lease.lease_until = lease.heartbeat_at = None
        lease.work_kind = lease.work_id = None

    @staticmethod
    def _state(target: WorkRow) -> dict[str, object]:
        if isinstance(target, WorkerBackfill):
            return {"status": target.status, "consecutive_failures": target.consecutive_failures}
        return {
            "enabled": target.enabled,
            "blocked_reason": target.blocked_reason,
            "consecutive_failures": target.consecutive_failures,
        }

    @staticmethod
    def _audit(
        db: Session, work: WorkRef, action: str, data: dict[str, object], before: dict[str, object]
    ) -> None:
        db.add(
            AuditLog(
                workspace_id=work.feed.workspace_id,
                actor_id=None,
                action=action,
                resource_type="backfill_job"
                if work.kind == "backfill"
                else "market_data_subscription",
                resource_id=work.id,
                correlation_id=uuid4(),
                before_data=before,
                after_data=data,
            )
        )
