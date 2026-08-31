from uuid import uuid4

import pytest
from app.market_data.infrastructure.leases import FeedKey, LeaseStore, WorkRef
from app.market_data.infrastructure.models import WorkerBackfill, WorkerSubscription
from app.models.catalog import BackfillJob, MarketDataSubscription
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
    for legacy, worker, column in (
        (BackfillJob, WorkerBackfill, "next_fetch_at"),
        (MarketDataSubscription, WorkerSubscription, "blocked_reason"),
    ):
        assert column not in str(select(legacy).compile(dialect=postgresql.dialect()))
        assert column in str(select(worker).compile(dialect=postgresql.dialect()))
        assert legacy.metadata is not worker.metadata


@pytest.mark.parametrize("limit", [0, 1001])
def test_recovery_batch_is_bounded_without_opening_a_connection(limit):
    with pytest.raises(ValueError, match="limit"):
        LeaseStore(sessionmaker()).recover_expired(limit)
