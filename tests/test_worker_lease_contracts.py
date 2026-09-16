from uuid import uuid4

import pytest
from app.market_data.infrastructure.leases import FeedKey, LeaseStore, WorkRef
from app.market_data.infrastructure.models import WorkerBackfill, WorkerSubscription
from app.models.market_data import BackfillJob, MarketDataSubscription
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker


@pytest.mark.parametrize("seconds", [0, -1, 3601])
def test_lease_duration_is_bounded_without_opening_a_connection(seconds):
    with pytest.raises(ValueError, match="lease_seconds"):
        LeaseStore(sessionmaker(), seconds)


def test_work_kind_cannot_fall_through_to_polling():
    with pytest.raises(ValueError, match="work kind"):
        WorkRef(FeedKey(uuid4(), uuid4(), "1m"), "unknown", uuid4())


def test_new_worker_mappings_do_not_change_legacy_api_queries():
    """Internal cursor state (next_fetch_at/scan_to) stays Worker-only; the (5) status fields
    (next_run_at/consecutive_failures/blocked_reason) are deliberately exposed on both."""
    for legacy, worker, internal_only_column in (
        (BackfillJob, WorkerBackfill, "next_fetch_at"),
        (MarketDataSubscription, WorkerSubscription, "scan_to"),
    ):
        legacy_sql = str(select(legacy).compile(dialect=postgresql.dialect()))
        worker_sql = str(select(worker).compile(dialect=postgresql.dialect()))
        assert internal_only_column not in legacy_sql
        assert internal_only_column in worker_sql
        assert legacy.metadata is not worker.metadata
    for shared_column in ("next_run_at", "consecutive_failures"):
        assert shared_column in str(select(BackfillJob).compile(dialect=postgresql.dialect()))
        assert shared_column in str(
            select(MarketDataSubscription).compile(dialect=postgresql.dialect())
        )
    assert "blocked_reason" in str(
        select(MarketDataSubscription).compile(dialect=postgresql.dialect())
    )


@pytest.mark.parametrize("limit", [0, 1001])
def test_recovery_batch_is_bounded_without_opening_a_connection(limit):
    with pytest.raises(ValueError, match="limit"):
        LeaseStore(sessionmaker()).recover_expired(limit)
