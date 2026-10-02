"""`app/trading/worker/__main__.py`: a public price refresh problem must never
stop the evaluation pass that follows it (docs/plans/paper-trading-live-data.md
Unit 1)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.trading.worker import __main__ as worker


@pytest.mark.anyio
async def test_an_unexpected_refresh_error_is_rolled_back_and_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker, "refresh_public_prices_for_active_bots", AsyncMock(side_effect=RuntimeError("db"))
    )
    db = MagicMock()

    await worker._refresh_public_prices(db, MagicMock())

    db.rollback.assert_called_once()


@pytest.mark.anyio
async def test_a_successful_refresh_leaves_the_session_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh = AsyncMock(return_value=1)
    monkeypatch.setattr(worker, "refresh_public_prices_for_active_bots", refresh)
    db = MagicMock()

    await worker._refresh_public_prices(db, MagicMock())

    refresh.assert_awaited_once()
    db.rollback.assert_not_called()
