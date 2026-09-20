from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.market_data.application.spread_tracking import record_spread_observation


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
    db.execute.assert_called_once()
    db.commit.assert_called_once()
