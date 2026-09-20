"""Tests for the OANDA PricingStream adapter: tick parsing, tick-to-candle
normalization, and the thread/event-loop wiring into a FeedHub feed. See
docs/design/modules/realtime-market-data-stream.md section 6 and
docs/plans/realtime-market-data-stream.md (work unit 3, RT-09)."""

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from app.exchanges.oanda import OandaApiError
from app.market_data.infrastructure.candle_stream import FeedHub, StreamFeedKey
from app.market_data.infrastructure.oanda_stream import (
    OandaStreamError,
    TickToCandleNormalizer,
    iter_oanda_price_ticks,
    make_oanda_feed_starter,
    parse_price_tick,
)
from oandapyV20.exceptions import V20Error


def _fake_client(lines: list[dict]) -> MagicMock:
    """Fakes API().request(stream) as a real generator over `lines`, so that
    PricingStream.terminate() -- which calls stream.response.throw(...) --
    behaves exactly as it does against the real SDK."""
    client = MagicMock()

    def request(endpoint):
        def generator():
            yield from lines

        live_generator = generator()
        endpoint.response = live_generator
        return live_generator

    client.request.side_effect = request
    return client


# ---------------------------------------------------------------------------
# parse_price_tick
# ---------------------------------------------------------------------------


def test_parse_price_tick_uses_bid_ask_midpoint() -> None:
    payload = {
        "type": "PRICE",
        "time": "2026-09-17T01:00:00.123456789Z",
        "bids": [{"price": "150.100"}],
        "asks": [{"price": "150.120"}],
    }
    tick = parse_price_tick(payload)
    assert tick is not None
    assert tick.mid == Decimal("150.110")


def test_parse_price_tick_ignores_heartbeats_and_malformed_lines() -> None:
    assert parse_price_tick({"type": "HEARTBEAT", "time": "2026-09-17T01:00:00Z"}) is None
    assert parse_price_tick({"type": "PRICE"}) is None
    assert parse_price_tick("not a dict") is None
    assert (
        parse_price_tick({"type": "PRICE", "time": "x", "bids": [], "asks": [{"price": "1"}]})
        is None
    )


# ---------------------------------------------------------------------------
# TickToCandleNormalizer -- RT-09
# ---------------------------------------------------------------------------


def test_normalizer_emits_one_provisional_update_per_tick_within_a_bucket() -> None:
    normalizer = TickToCandleNormalizer("1m")
    first = normalizer.add_tick(datetime(2026, 9, 17, 1, 0, 5, tzinfo=UTC), Decimal("150.10"))
    assert len(first) == 1
    assert first[0].is_final is False
    assert first[0].open_time == datetime(2026, 9, 17, 1, 0, 0, tzinfo=UTC)
    assert first[0].ohlcv.volume == Decimal(1)

    second = normalizer.add_tick(datetime(2026, 9, 17, 1, 0, 15, tzinfo=UTC), Decimal("150.20"))
    assert len(second) == 1
    assert second[0].ohlcv.open == Decimal("150.10")
    assert second[0].ohlcv.high == Decimal("150.20")
    assert second[0].ohlcv.volume == Decimal(2)


def test_normalizer_finalizes_the_previous_bucket_on_boundary_crossing() -> None:
    """RT-09: the first tick that crosses a timeframe boundary finalizes the
    previous bucket and opens a new provisional one."""
    normalizer = TickToCandleNormalizer("1m")
    normalizer.add_tick(datetime(2026, 9, 17, 1, 0, 5, tzinfo=UTC), Decimal("150.10"))
    normalizer.add_tick(datetime(2026, 9, 17, 1, 0, 45, tzinfo=UTC), Decimal("150.20"))

    updates = normalizer.add_tick(datetime(2026, 9, 17, 1, 1, 1, tzinfo=UTC), Decimal("150.30"))

    assert len(updates) == 2
    finalized, provisional = updates
    assert finalized.is_final is True
    assert finalized.open_time == datetime(2026, 9, 17, 1, 0, 0, tzinfo=UTC)
    assert finalized.ohlcv.close == Decimal("150.20")
    assert provisional.is_final is False
    assert provisional.open_time == datetime(2026, 9, 17, 1, 1, 0, tzinfo=UTC)
    assert provisional.ohlcv.open == Decimal("150.30")


def test_normalizer_drops_stale_out_of_order_ticks() -> None:
    normalizer = TickToCandleNormalizer("1m")
    normalizer.add_tick(datetime(2026, 9, 17, 1, 1, 0, tzinfo=UTC), Decimal("150.10"))
    stale = normalizer.add_tick(datetime(2026, 9, 17, 1, 0, 30, tzinfo=UTC), Decimal("999"))
    assert stale == []


def test_normalizer_rejects_unsupported_timeframe() -> None:
    try:
        TickToCandleNormalizer("2m")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unsupported timeframe")


# ---------------------------------------------------------------------------
# iter_oanda_price_ticks
# ---------------------------------------------------------------------------

_LINES = [
    {"type": "HEARTBEAT", "time": "2026-09-17T01:00:00Z"},
    {
        "type": "PRICE",
        "time": "2026-09-17T01:00:01Z",
        "bids": [{"price": "150.10"}],
        "asks": [{"price": "150.12"}],
    },
    {
        "type": "PRICE",
        "time": "2026-09-17T01:00:02Z",
        "bids": [{"price": "150.20"}],
        "asks": [{"price": "150.22"}],
    },
]


def test_iter_oanda_price_ticks_skips_heartbeats_and_preserves_order() -> None:
    api_factory = MagicMock(return_value=_fake_client(_LINES))
    ticks = list(
        iter_oanda_price_ticks(
            base_url="https://api-fxpractice.oanda.com",
            token="private-token",
            account_id="101-001-1-001",
            symbol="USD_JPY",
            stop_event=threading.Event(),
            api_factory=api_factory,
        )
    )
    assert [str(tick.mid) for tick in ticks] == ["150.11", "150.21"]


def test_iter_oanda_price_ticks_stops_gracefully_when_requested() -> None:
    stop_event = threading.Event()
    stop_event.set()
    api_factory = MagicMock(return_value=_fake_client(_LINES))
    ticks = list(
        iter_oanda_price_ticks(
            base_url="https://api-fxpractice.oanda.com",
            token="private-token",
            account_id="101-001-1-001",
            symbol="USD_JPY",
            stop_event=stop_event,
            api_factory=api_factory,
        )
    )
    assert ticks == []


def test_iter_oanda_price_ticks_rejects_non_practice_host() -> None:
    try:
        list(
            iter_oanda_price_ticks(
                base_url="https://example.com",
                token="private-token",
                account_id="101-001-1-001",
                symbol="USD_JPY",
                stop_event=threading.Event(),
            )
        )
    except OandaApiError:
        pass
    else:
        raise AssertionError("expected a non-practice host to be rejected")


def test_iter_oanda_price_ticks_maps_authentication_failure() -> None:
    client = MagicMock()

    def raise_401(endpoint):
        raise V20Error(401, "unauthorized")

    client.request.side_effect = raise_401
    try:
        list(
            iter_oanda_price_ticks(
                base_url="https://api-fxpractice.oanda.com",
                token="private-token",
                account_id="101-001-1-001",
                symbol="USD_JPY",
                stop_event=threading.Event(),
                api_factory=MagicMock(return_value=client),
            )
        )
    except OandaStreamError as exc:
        assert exc.code == "oanda_authentication_failed"
    else:
        raise AssertionError("expected OandaStreamError")


# ---------------------------------------------------------------------------
# OandaFeedWorker / make_oanda_feed_starter -- thread -> event loop handoff
# ---------------------------------------------------------------------------


@dataclass
class _FakeTick:
    time: datetime
    mid: Decimal


def _fake_tick_source(*, base_url, token, account_id, symbol, stop_event):
    ticks = [
        _FakeTick(datetime(2026, 9, 17, 2, 0, 1, tzinfo=UTC), Decimal("150.0")),
        _FakeTick(datetime(2026, 9, 17, 2, 0, 2, tzinfo=UTC), Decimal("150.5")),
        _FakeTick(datetime(2026, 9, 17, 2, 1, 1, tzinfo=UTC), Decimal("151.0")),
    ]
    for tick in ticks:
        if stop_event.is_set():
            return
        yield tick


def test_oanda_feed_worker_publishes_normalized_events_through_a_feed_hub() -> None:
    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("oanda", "USD_JPY", "1m")
        starter = make_oanda_feed_starter(
            base_url="https://api-fxpractice.oanda.com",
            token="private-token",
            account_id="101-001-1-001",
            loop=asyncio.get_running_loop(),
            tick_source=_fake_tick_source,
        )
        sub = await hub.subscribe(key, source="oanda_practice", starter=starter)
        events = [await asyncio.wait_for(sub.queue.get(), timeout=2.0) for _ in range(4)]
        assert [event.event_type for event in events] == [
            "provisional_update",
            "provisional_update",
            "candle_finalized",
            "provisional_update",
        ]
        assert [event.sequence for event in events] == [0, 1, 2, 3]
        await sub.close()

    asyncio.run(scenario())


def test_make_oanda_feed_starter_rejects_a_non_oanda_feed_key() -> None:
    starter = make_oanda_feed_starter(
        base_url="https://api-fxpractice.oanda.com",
        token="private-token",
        account_id="101-001-1-001",
        loop=asyncio.new_event_loop(),
    )
    try:
        starter(StreamFeedKey("binance", "BTCJPY", "1m"), MagicMock())
    except ValueError:
        pass
    else:
        raise AssertionError("expected a non-oanda feed key to be rejected")


def test_oanda_feed_worker_reports_stream_errors_as_gap_notice() -> None:
    def failing_tick_source(*, base_url, token, account_id, symbol, stop_event):
        raise OandaStreamError("oanda_unreachable", "boom")
        yield  # pragma: no cover - never reached; makes this a generator function

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("oanda", "USD_JPY", "1m")
        starter = make_oanda_feed_starter(
            base_url="https://api-fxpractice.oanda.com",
            token="private-token",
            account_id="101-001-1-001",
            loop=asyncio.get_running_loop(),
            tick_source=failing_tick_source,
        )
        sub = await hub.subscribe(key, source="oanda_practice", starter=starter)
        event = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert event.event_type == "gap_notice"
        assert event.reason_code == "oanda_unreachable"
        assert hub.feed_state(key) == "disconnected"
        await sub.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# OandaFeedWorker.record_spread / make_oanda_feed_starter(record_spread=...)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSpreadTick:
    time: datetime
    mid: Decimal
    bid: Decimal
    ask: Decimal


def _fake_spread_tick_source(*, base_url, token, account_id, symbol, stop_event):
    ticks = [
        _FakeSpreadTick(
            datetime(2026, 9, 20, 2, 0, 1, tzinfo=UTC),
            Decimal("150.0"),
            Decimal("149.9"),
            Decimal("150.1"),
        ),
        _FakeSpreadTick(
            datetime(2026, 9, 20, 2, 0, 2, tzinfo=UTC),
            Decimal("150.5"),
            Decimal("150.4"),
            Decimal("150.6"),
        ),
    ]
    for tick in ticks:
        if stop_event.is_set():
            return
        yield tick


def test_record_spread_is_called_once_per_tick_with_symbol_and_tick() -> None:
    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("oanda", "USD_JPY", "1m")
        calls: list[tuple[str, _FakeSpreadTick]] = []
        starter = make_oanda_feed_starter(
            base_url="https://api-fxpractice.oanda.com",
            token="private-token",
            account_id="101-001-1-001",
            loop=asyncio.get_running_loop(),
            tick_source=_fake_spread_tick_source,
            record_spread=lambda symbol, tick: calls.append((symbol, tick)),
        )
        sub = await hub.subscribe(key, source="oanda_practice", starter=starter)
        await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        # give the background thread a moment to run past the 2nd tick's record_spread
        await asyncio.sleep(0.05)
        assert [symbol for symbol, _ in calls] == ["USD_JPY", "USD_JPY"]
        assert [tick.bid for _, tick in calls] == [Decimal("149.9"), Decimal("150.4")]
        assert [tick.ask for _, tick in calls] == [Decimal("150.1"), Decimal("150.6")]
        await sub.close()

    asyncio.run(scenario())


def test_record_spread_failure_does_not_break_the_candle_stream() -> None:
    def failing_record_spread(symbol, tick):
        raise RuntimeError("db unavailable")

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("oanda", "USD_JPY", "1m")
        starter = make_oanda_feed_starter(
            base_url="https://api-fxpractice.oanda.com",
            token="private-token",
            account_id="101-001-1-001",
            loop=asyncio.get_running_loop(),
            tick_source=_fake_spread_tick_source,
            record_spread=failing_record_spread,
        )
        sub = await hub.subscribe(key, source="oanda_practice", starter=starter)
        # Candle events still arrive even though every record_spread call raises.
        event = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert event.event_type == "provisional_update"
        await sub.close()

    asyncio.run(scenario())
