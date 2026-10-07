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


@pytest.mark.anyio
async def test_a_failing_pass_does_not_end_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    passes = []

    def failing_pass(db, tracker):
        passes.append(tracker)
        if len(passes) == 1:
            raise RuntimeError("database unavailable")
        stop.set()
        return 0

    stop = asyncio.Event()
    monkeypatch.setattr(worker, "run_active_bots_once", failing_pass)
    monkeypatch.setattr(worker, "get_binance_public_client", MagicMock)
    monkeypatch.setattr(worker, "SessionLocal", MagicMock())
    monkeypatch.setattr(worker, "_refresh_public_prices", AsyncMock())
    monkeypatch.setattr(worker.settings, "bot_execution_poll_interval_seconds", 0.01)

    await worker._run(stop)

    assert len(passes) == 2  # the second pass ran after the first one raised
    assert passes[0] is passes[1]  # one tracker for the life of the worker
