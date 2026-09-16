"""One-time, re-runnable cutover step. Never invoked automatically by the Worker loop."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_data.infrastructure.models import WorkerBackfill
from app.models.audit import AuditLog


@dataclass(frozen=True)
class NormalizationResult:
    reset_count: int
    skipped_already_migrated_count: int


def normalize_legacy_jobs(db: Session) -> NormalizationResult:
    """Return stale pre-cutover running/validating jobs to queued so the Worker finds them.

    Jobs that already have a `next_fetch_at` checkpoint were touched by the new
    lease-based path and must not be reinitialized. Idempotent: safe to re-run.
    """
    now = datetime.now(UTC)
    candidates = db.scalars(
        select(WorkerBackfill).where(WorkerBackfill.status.in_(("running", "validating")))
    ).all()
    reset = skipped = 0
    for job in candidates:
        if job.next_fetch_at is not None:
            skipped += 1
            continue
        before = {"status": job.status}
        job.status = "queued"
        job.finished_at = None
        job.next_run_at = now
        db.add(
            AuditLog(
                workspace_id=job.workspace_id,
                actor_id=None,
                action="market_data.legacy_job_normalized",
                resource_type="backfill_job",
                resource_id=job.id,
                correlation_id=uuid4(),
                before_data=before,
                after_data={"status": "queued"},
            )
        )
        reset += 1
    db.commit()
    return NormalizationResult(reset, skipped)
