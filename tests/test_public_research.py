"""`app/market_data/application/public_research.py` (2026-09-26): provisioning
and backfill for the "binance_public" research-only `Instrument`.
`BinancePublicClient` is monkeypatched throughout -- these tests are about the
provisioning/upsert orchestration, not the HTTP client itself (covered in
tests/test_binance_public.py).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.exchanges.binance import BinanceInstrumentRules
from app.exchanges.types import CandlePoint
from app.market_data.application import public_research
from app.models.connections import Exchange, Market
from app.models.instruments import Instrument


def _rules(**overrides: object) -> BinanceInstrumentRules:
    defaults: dict[str, object] = dict(
        symbol="BTCJPY",
        base_asset="BTC",
        quote_asset="JPY",
        price_scale=0,
        quantity_scale=6,
        tick_size=Decimal(1),
        step_size=Decimal("0.000001"),
        min_quantity=Decimal("0.0001"),
        max_quantity=Decimal(1000),
        min_notional=Decimal(100),
        allowed_order_types=("LIMIT", "MARKET"),
    )
    defaults.update(overrides)
    return BinanceInstrumentRules(**defaults)  # type: ignore[arg-type]


def _candle(i: int, *, is_final: bool = True) -> CandlePoint:
    t = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=i)
    return CandlePoint(
        open_time=t,
        close_time=t + timedelta(hours=1),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(10),
        trade_count=5,
        is_final=is_final,
    )


# ---- ensure_public_research_instrument ----


@pytest.mark.anyio
async def test_ensure_public_research_instrument_raises_when_exchange_not_seeded() -> None:
    db = MagicMock()
    db.scalar.return_value = None  # Exchange lookup
    client = MagicMock()

    with pytest.raises(public_research.PublicResearchError) as exc:
        await public_research.ensure_public_research_instrument(db, client)
    assert exc.value.code == "exchange_not_seeded"


@pytest.mark.anyio
async def test_ensure_public_research_instrument_raises_when_market_not_seeded() -> None:
    db = MagicMock()
    db.scalar.side_effect = [Exchange(id=uuid4(), code="binance_public"), None]
    client = MagicMock()

    with pytest.raises(public_research.PublicResearchError) as exc:
        await public_research.ensure_public_research_instrument(db, client)
    assert exc.value.code == "market_not_seeded"


@pytest.mark.anyio
async def test_ensure_public_research_instrument_creates_a_new_instrument() -> None:
    exchange = Exchange(id=uuid4(), code="binance_public")
    market = Market(id=uuid4(), code="crypto_spot")
    db = MagicMock()
    db.scalar.side_effect = [exchange, market, None]  # exchange, market, existing-instrument
    client = MagicMock()
    client.get_instrument_rules = AsyncMock(return_value=_rules())

    instrument = await public_research.ensure_public_research_instrument(db, client, "BTCJPY")

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added is instrument
    assert instrument.exchange_id == exchange.id
    assert instrument.market_id == market.id
    assert instrument.symbol == "BTCJPY"
    assert instrument.base_asset == "BTC"
    assert instrument.min_notional == Decimal(100)
    assert instrument.capabilities == {"research_only": True, "source": "binance_public"}
    assert instrument.status == "active"
    db.flush.assert_called_once()


@pytest.mark.anyio
async def test_ensure_public_research_instrument_refreshes_an_existing_instrument() -> None:
    exchange = Exchange(id=uuid4(), code="binance_public")
    market = Market(id=uuid4(), code="crypto_spot")
    existing = Instrument(
        id=uuid4(),
        exchange_id=exchange.id,
        market_id=market.id,
        symbol="BTCJPY",
        base_asset="BTC",
        quote_asset="JPY",
        price_scale=0,
        quantity_scale=6,
        tick_size=Decimal(1),
        step_size=Decimal("0.000001"),
        min_notional=Decimal(50),
    )
    db = MagicMock()
    db.scalar.side_effect = [exchange, market, existing]
    client = MagicMock()
    client.get_instrument_rules = AsyncMock(return_value=_rules(min_notional=Decimal(200)))

    instrument = await public_research.ensure_public_research_instrument(db, client, "BTCJPY")

    assert instrument is existing
    assert instrument.min_notional == Decimal(200)
    db.add.assert_not_called()


# ---- backfill_public_klines ----


@pytest.mark.anyio
async def test_backfill_public_klines_upserts_only_final_candles_within_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = Instrument(id=uuid4(), symbol="BTCJPY")
    db = MagicMock()
    client = MagicMock()
    points = [_candle(0), _candle(1, is_final=False)]
    client.get_candles = AsyncMock(return_value=points)
    upsert = MagicMock(return_value=(1, 0))
    monkeypatch.setattr(public_research, "upsert_candle_points", upsert)

    inserted, updated = await public_research.backfill_public_klines(
        db, client, instrument, "1h", days=1
    )

    assert (inserted, updated) == (1, 0)
    upsert.assert_called_once()
    call_args = upsert.call_args[0]
    assert call_args[0] is db
    assert call_args[1] == instrument.id
    assert call_args[2] == "1h"
    assert call_args[3] == "binance_public"
    assert call_args[4] == "backfilled"
    assert call_args[5] == [points[0]]  # the non-final candle was filtered out
    db.commit.assert_called_once()


@pytest.mark.anyio
async def test_backfill_public_klines_pages_across_a_multi_day_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = Instrument(id=uuid4(), symbol="BTCJPY")
    db = MagicMock()
    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])
    monkeypatch.setattr(public_research, "_PAGE_SIZE_CANDLES", 24)

    inserted, updated = await public_research.backfill_public_klines(
        db, client, instrument, "1h", days=3
    )

    assert (inserted, updated) == (0, 0)
    assert client.get_candles.call_count == 3  # 3 days * 24h / 24-candle pages
    db.commit.assert_not_called()  # no final points on any page
