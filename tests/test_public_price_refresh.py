"""`refresh_public_prices_for_active_bots` (docs/plans/paper-trading-live-data.md
Unit 1): which instruments get refreshed, and that one instrument's fetch
failure neither stops the others nor leaves the session unusable. The
per-instrument fetch itself (`refresh_public_klines`) is covered in
tests/test_public_research.py."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.exchanges.binance_public import BinancePublicApiError
from app.models.instruments import Instrument
from app.trading.application import public_price_refresh

NOW = datetime(2026, 10, 1, 8, tzinfo=UTC)


@pytest.mark.anyio
async def test_refreshes_every_public_instrument_and_timeframe_of_an_active_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    btc, eth = Instrument(id=uuid4(), symbol="BTCUSDT"), Instrument(id=uuid4(), symbol="ETHUSDT")
    db = MagicMock()
    db.execute.return_value.all.return_value = [(btc, "4h"), (eth, "4h")]
    refresh = AsyncMock(return_value=1)
    monkeypatch.setattr(public_price_refresh, "refresh_public_klines", refresh)
    client = MagicMock()

    refreshed = await public_price_refresh.refresh_public_prices_for_active_bots(
        db, client, now=NOW
    )

    assert refreshed == 2
    calls = [(c.args[2].symbol, c.args[3], c.kwargs) for c in refresh.call_args_list]
    assert calls == [
        ("BTCUSDT", "4h", {"initial_bars": public_price_refresh.INITIAL_BARS, "now": NOW}),
        ("ETHUSDT", "4h", {"initial_bars": public_price_refresh.INITIAL_BARS, "now": NOW}),
    ]


@pytest.mark.anyio
async def test_one_instruments_failure_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    btc, eth = Instrument(id=uuid4(), symbol="BTCUSDT"), Instrument(id=uuid4(), symbol="ETHUSDT")
    db = MagicMock()
    db.execute.return_value.all.return_value = [(btc, "4h"), (eth, "4h")]
    refresh = AsyncMock(side_effect=[BinancePublicApiError("unreachable"), 1])
    monkeypatch.setattr(public_price_refresh, "refresh_public_klines", refresh)

    refreshed = await public_price_refresh.refresh_public_prices_for_active_bots(
        db, MagicMock(), now=NOW
    )

    assert refreshed == 1
    assert refresh.call_count == 2
    db.rollback.assert_called_once()


@pytest.mark.anyio
async def test_no_active_public_bots_means_no_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    refresh = AsyncMock()
    monkeypatch.setattr(public_price_refresh, "refresh_public_klines", refresh)

    assert (
        await public_price_refresh.refresh_public_prices_for_active_bots(db, MagicMock(), now=NOW)
        == 0
    )
    refresh.assert_not_called()


def test_initial_history_covers_the_strategy_lookback() -> None:
    from app.trading.application.backtest_replay import _HISTORY_WINDOW

    assert public_price_refresh.INITIAL_BARS >= _HISTORY_WINDOW
