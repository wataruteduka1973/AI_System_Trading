from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.market_data.application.spread_tracking import record_spread_observation
from sqlalchemy.exc import OperationalError


def test_record_spread_observation_executes_and_commits() -> None:
    db = MagicMock()
    instrument_id = uuid4()
    record_spread_observation(
        db,
        instrument_id,
        bid=Decimal("149.9"),
        ask=Decimal("150.1"),
        observed_at=datetime(2026, 9, 20, 2, 0, 0, tzinfo=UTC),
        source="oanda_practice",
    )
    assert db.execute.call_count == 2  # the latest-value upsert, then the history append
    assert db.commit.call_count == 2


def _record(db: MagicMock) -> None:
    record_spread_observation(
        db,
        uuid4(),
        bid=Decimal("149.9"),
        ask=Decimal("150.1"),
        observed_at=datetime(2026, 9, 20, 2, 0, 0, tzinfo=UTC),
        source="oanda_practice",
    )


def test_the_history_append_is_sampled_by_a_five_minute_window() -> None:
    db = MagicMock()
    _record(db)

    history_statement, params = db.execute.call_args_list[1].args
    sql = str(history_statement)
    assert "fx.instrument_spread_history" in sql
    assert "NOT EXISTS" in sql  # skipped when a row is already inside the window
    assert params["seconds"] == 300
    assert params["source"] == "oanda_practice"


def test_a_history_failure_leaves_the_latest_value_stored_and_does_not_raise() -> None:
    """Until `alembic upgrade head` has run the history table does not exist; the latest
    value fills and the Risk Gate read must keep working."""
    db = MagicMock()
    db.execute.side_effect = [None, OperationalError("INSERT", {}, Exception("no such table"))]

    _record(db)

    assert db.commit.call_count == 1  # the upsert was committed before the history attempt
    db.rollback.assert_called_once_with()
