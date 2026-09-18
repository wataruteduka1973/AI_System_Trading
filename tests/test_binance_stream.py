"""Tests for the Binance adapter: kline message parsing and the (thread-
free) asyncio.Task wiring into a FeedHub feed. See
docs/design/modules/realtime-market-data-stream.md section 6 and
docs/plans/realtime-market-data-stream.md (work unit 4)."""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

from app.exchanges.binance import BinanceApiError
from app.market_data.infrastructure.binance_stream import (
    BinanceFeedWorker,
    make_binance_feed_starter,
    parse_kline_message,
)
from app.market_data.infrastructure.candle_stream import FeedHub, StreamFeedKey
from binance.exceptions import ReadLoopClosed

TESTNET_BASE_URL = "https://testnet.binance.vision"


def _kline(open_time_ms: int, *, o: str, h: str, low: str, c: str, v: str, is_closed: bool) -> dict:
    return {
        "e": "kline",
        "E": open_time_ms + 500,
        "s": "BTCJPY",
        "k": {
            "t": open_time_ms,
            "T": open_time_ms + 59_999,
            "s": "BTCJPY",
            "i": "1m",
            "o": o,
            "c": c,
            "h": h,
            "l": low,
            "v": v,
            "n": 10,
            "x": is_closed,
            "q": "0",
            "V": "0",
            "Q": "0",
            "B": "0",
        },
    }


# ---------------------------------------------------------------------------
# parse_kline_message
# ---------------------------------------------------------------------------


def test_parse_kline_message_extracts_ohlcv_and_is_final() -> None:
    provisional_payload = _kline(
        1_700_000_000_000, o="100.0", h="100.5", low="99.5", c="100.2", v="1.0", is_closed=False
    )
    provisional = parse_kline_message(provisional_payload)
    assert provisional is not None
    assert provisional.is_final is False
    assert provisional.ohlcv.close == Decimal("100.2")

    final_payload = _kline(
        1_700_000_000_000, o="100.0", h="101.0", low="99.5", c="100.8", v="2.5", is_closed=True
    )
    final = parse_kline_message(final_payload)
    assert final is not None
    assert final.is_final is True
    assert final.ohlcv.volume == Decimal("2.5")


def test_parse_kline_message_ignores_non_kline_and_malformed_payloads() -> None:
    assert parse_kline_message("not a dict") is None
    assert parse_kline_message({"e": "error", "type": "X", "m": "boom"}) is None
    assert parse_kline_message({"e": "kline"}) is None  # missing "k"
    incomplete_kline = {"t": 1, "o": "1", "h": "1", "l": "1", "x": False}  # missing "c"/"v"
    assert parse_kline_message({"e": "kline", "k": incomplete_kline}) is None
    assert (
        parse_kline_message(_kline(0, o="0", h="0", low="0", c="0", v="0", is_closed=False))
        is None
    )  # non-positive price


# ---------------------------------------------------------------------------
# BinanceFeedWorker / make_binance_feed_starter -- construction-time checks
# ---------------------------------------------------------------------------


def test_binance_feed_worker_rejects_unsupported_timeframe() -> None:
    try:
        BinanceFeedWorker(symbol="BTCJPY", timeframe="2m", sink=MagicMock())
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unsupported timeframe")


def test_make_binance_feed_starter_rejects_non_testnet_base_url() -> None:
    try:
        make_binance_feed_starter(base_url="https://api.binance.com")
    except BinanceApiError:
        pass
    else:
        raise AssertionError("expected a non-testnet base_url to be rejected")


def test_make_binance_feed_starter_rejects_a_non_binance_feed_key() -> None:
    starter = make_binance_feed_starter(base_url=TESTNET_BASE_URL)
    try:
        starter(StreamFeedKey("oanda", "USD_JPY", "1m"), MagicMock())
    except ValueError:
        pass
    else:
        raise AssertionError("expected a non-binance feed key to be rejected")


# ---------------------------------------------------------------------------
# BinanceFeedWorker -- thread-free asyncio.Task wiring, via fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    async def close_connection(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self, messages: list, *, error_after: bool = False) -> None:
        self._messages = list(messages)
        self._index = 0
        self._error_after = error_after

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def recv(self) -> dict:
        if self._index < len(self._messages):
            message = self._messages[self._index]
            self._index += 1
            return message
        if self._error_after:
            raise ReadLoopClosed("fake read loop closed")
        await asyncio.sleep(3600)  # simulate "no more messages yet"


class _FakeSocketManager:
    def __init__(self, client: _FakeClient, *, messages: list, error_after: bool = False) -> None:
        self._client = client
        self._messages = messages
        self._error_after = error_after

    def kline_socket(self, symbol: str, interval: str) -> _FakeStream:
        return _FakeStream(self._messages, error_after=self._error_after)


def test_binance_feed_worker_publishes_normalized_events_through_a_feed_hub() -> None:
    open_ms = 1_700_000_000_000
    messages = [
        _kline(open_ms, o="100.0", h="100.5", low="99.5", c="100.2", v="1.0", is_closed=False),
        _kline(open_ms, o="100.0", h="101.0", low="99.5", c="100.8", v="2.5", is_closed=True),
        _kline(
            open_ms + 60_000, o="100.8", h="100.9", low="100.7", c="100.9", v="0.5", is_closed=False
        ),
    ]
    client = _FakeClient()

    async def client_factory(**kwargs):
        return client

    def socket_manager_factory(created_client):
        assert created_client is client
        return _FakeSocketManager(created_client, messages=messages)

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("binance", "BTCJPY", "1m")
        starter = make_binance_feed_starter(
            base_url=TESTNET_BASE_URL,
            client_factory=client_factory,
            socket_manager_factory=socket_manager_factory,
        )
        sub = await hub.subscribe(key, source="binance_testnet", starter=starter)
        events = [await asyncio.wait_for(sub.queue.get(), timeout=2.0) for _ in range(3)]
        assert [event.event_type for event in events] == [
            "provisional_update",
            "candle_finalized",
            "provisional_update",
        ]
        assert [event.sequence for event in events] == [0, 1, 2]
        assert events[1].ohlcv.close == Decimal("100.8")
        assert events[2].ohlcv.open == Decimal("100.8")
        await sub.close()

    asyncio.run(scenario())


def test_binance_feed_worker_reports_connect_failure_as_gap_notice() -> None:
    async def failing_client_factory(**kwargs):
        raise TimeoutError("boom")

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("binance", "BTCJPY", "1m")
        starter = make_binance_feed_starter(
            base_url=TESTNET_BASE_URL,
            client_factory=failing_client_factory,
        )
        sub = await hub.subscribe(key, source="binance_testnet", starter=starter)
        event = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert event.event_type == "gap_notice"
        assert event.reason_code == "binance_unreachable"
        assert hub.feed_state(key) == "disconnected"
        await sub.close()

    asyncio.run(scenario())


def test_binance_feed_worker_reports_stream_error_as_gap_notice() -> None:
    open_ms = 1_700_000_000_000
    messages = [
        _kline(open_ms, o="100.0", h="100.5", low="99.5", c="100.2", v="1.0", is_closed=False),
        {"e": "error", "type": "ConnectionClosedError", "m": "boom"},
    ]
    client = _FakeClient()

    async def client_factory(**kwargs):
        return client

    def socket_manager_factory(created_client):
        return _FakeSocketManager(created_client, messages=messages)

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("binance", "BTCJPY", "1m")
        starter = make_binance_feed_starter(
            base_url=TESTNET_BASE_URL,
            client_factory=client_factory,
            socket_manager_factory=socket_manager_factory,
        )
        sub = await hub.subscribe(key, source="binance_testnet", starter=starter)
        first = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert first.event_type == "provisional_update"
        second = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert second.event_type == "gap_notice"
        assert second.reason_code == "binance_stream_disconnected"
        assert hub.feed_state(key) == "disconnected"
        await sub.close()

    asyncio.run(scenario())


def test_binance_feed_worker_reports_read_loop_closed_as_gap_notice() -> None:
    open_ms = 1_700_000_000_000
    messages = [
        _kline(open_ms, o="100.0", h="100.5", low="99.5", c="100.2", v="1.0", is_closed=False),
    ]
    client = _FakeClient()

    async def client_factory(**kwargs):
        return client

    def socket_manager_factory(created_client):
        return _FakeSocketManager(created_client, messages=messages, error_after=True)

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=1.0)
        key = StreamFeedKey("binance", "BTCJPY", "1m")
        starter = make_binance_feed_starter(
            base_url=TESTNET_BASE_URL,
            client_factory=client_factory,
            socket_manager_factory=socket_manager_factory,
        )
        sub = await hub.subscribe(key, source="binance_testnet", starter=starter)
        first = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert first.event_type == "provisional_update"
        second = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert second.event_type == "gap_notice"
        assert second.reason_code == "binance_stream_disconnected"
        assert client.closed is True
        await sub.close()

    asyncio.run(scenario())
