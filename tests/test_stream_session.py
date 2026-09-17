"""Integration-style tests for app.market_data.infrastructure.stream_session
-- the framework-independent WS-connection orchestrator (ticket verify,
credential/starter resolution, FeedHub subscribe, resume decision,
heartbeat/event pump). Genuinely runs the whole flow with a fake Transport
and a fake FeedStarter (no network, no real WebSocket/DB -- see
docs/plans/realtime-market-data-stream.md work units 5/6 verification
notes for why the real FastAPI adapter cannot be exercised the same way in
this sandbox)."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    FeedHub,
    FeedSink,
    FeedWorkerHandle,
    NormalizedCandleUpdate,
    StreamFeedKey,
)
from app.market_data.infrastructure.stream_session import (
    CLOSE_ACCESS_DENIED,
    CLOSE_FEED_START_FAILED,
    CLOSE_TICKET_REJECTED,
    Disconnected,
    StreamAccessDenied,
    StreamSessionParams,
    run_stream_session,
)
from app.market_data.infrastructure.stream_tickets import UsedTicketStore, issue_ticket

TICKET_SECRET = "test-signing-secret"
WORKSPACE_ID = uuid4()


class _FakeTransport:
    """Records everything sent; `disconnect_after` (if set) makes the Nth
    receive() call raise Disconnected, simulating the client closing the
    connection after that many idle waits."""

    def __init__(self, *, disconnect_after: int | None = None) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self._disconnect_after = disconnect_after
        self._receive_calls = 0
        self._disconnect_event = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def receive(self) -> None:
        self._receive_calls += 1
        if self._disconnect_after is not None and self._receive_calls >= self._disconnect_after:
            raise Disconnected
        await self._disconnect_event.wait()  # woken by trigger_disconnect(), if ever
        raise Disconnected

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)

    def trigger_disconnect(self) -> None:
        self._disconnect_event.set()


class _FakeWorkerHandle:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _make_immediate_starter(update: NormalizedCandleUpdate):
    """A FeedStarter that synchronously publishes one update the moment the
    feed starts, matching the same publish-before-return timing FeedHub's
    _start_feed docstring calls out (work unit 3's ordering-bug fix)."""

    def starter(key: StreamFeedKey, sink: FeedSink) -> FeedWorkerHandle:
        sink.publish_candle(update)
        return _FakeWorkerHandle()

    return starter


def _issue_ticket(*, exchange: str = "binance", symbol: str = "BTCJPY", timeframe: str = "1m"):
    token, _ = issue_ticket(
        TICKET_SECRET,
        workspace_id=WORKSPACE_ID,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        ttl_seconds=60,
    )
    return token


async def _run_with_timeout(coro, *, timeout: float = 2.0):
    return await asyncio.wait_for(coro, timeout=timeout)


@pytest.mark.anyio
async def test_rejects_an_invalid_ticket_before_ever_accepting() -> None:
    transport = _FakeTransport()

    async def build_starter(claims, loop):  # pragma: no cover - must not be called
        raise AssertionError("build_starter should not run for a rejected ticket")

    await _run_with_timeout(
        run_stream_session(
            transport,
            StreamSessionParams(ticket="not-a-real-ticket"),
            ticket_secret=TICKET_SECRET,
            used_tickets=UsedTicketStore(),
            hub=FeedHub(grace_period_seconds=0.01),
            build_starter=build_starter,
            heartbeat_interval_seconds=30.0,
        )
    )

    assert not transport.accepted
    assert transport.closed == (CLOSE_TICKET_REJECTED, "ticket_invalid")


@pytest.mark.anyio
async def test_rejects_a_ticket_already_used_once() -> None:
    used_tickets = UsedTicketStore()
    ticket = _issue_ticket()
    hub = FeedHub(grace_period_seconds=0.01)

    async def build_starter(claims, loop):
        return _make_immediate_starter(
            NormalizedCandleUpdate(datetime(2026, 9, 17, tzinfo=UTC), _ohlcv(), False)
        ), "binance_testnet"

    first = _FakeTransport(disconnect_after=1)
    await _run_with_timeout(
        run_stream_session(
            first,
            StreamSessionParams(ticket=ticket),
            ticket_secret=TICKET_SECRET,
            used_tickets=used_tickets,
            hub=hub,
            build_starter=build_starter,
            heartbeat_interval_seconds=0.05,
        )
    )
    assert first.accepted

    second = _FakeTransport()
    await _run_with_timeout(
        run_stream_session(
            second,
            StreamSessionParams(ticket=ticket),
            ticket_secret=TICKET_SECRET,
            used_tickets=used_tickets,
            hub=hub,
            build_starter=build_starter,
            heartbeat_interval_seconds=30.0,
        )
    )
    assert not second.accepted
    assert second.closed == (CLOSE_TICKET_REJECTED, "ticket_already_used")


@pytest.mark.anyio
async def test_access_denied_closes_without_starting_a_feed() -> None:
    transport = _FakeTransport()
    hub = FeedHub(grace_period_seconds=0.01)

    async def build_starter(claims, loop):
        raise StreamAccessDenied("credentials_missing")

    await _run_with_timeout(
        run_stream_session(
            transport,
            StreamSessionParams(ticket=_issue_ticket()),
            ticket_secret=TICKET_SECRET,
            used_tickets=UsedTicketStore(),
            hub=hub,
            build_starter=build_starter,
            heartbeat_interval_seconds=30.0,
        )
    )

    assert not transport.accepted
    assert transport.closed == (CLOSE_ACCESS_DENIED, "credentials_missing")
    assert hub.active_feed_count() == 0


@pytest.mark.anyio
async def test_feed_start_failure_closes_with_a_safe_code() -> None:
    transport = _FakeTransport()
    hub = FeedHub(grace_period_seconds=0.01)

    def failing_starter(key, sink):
        raise RuntimeError("boom -- should never leak into the close reason")

    async def build_starter(claims, loop):
        return failing_starter, "binance_testnet"

    await _run_with_timeout(
        run_stream_session(
            transport,
            StreamSessionParams(ticket=_issue_ticket()),
            ticket_secret=TICKET_SECRET,
            used_tickets=UsedTicketStore(),
            hub=hub,
            build_starter=build_starter,
            heartbeat_interval_seconds=30.0,
        )
    )

    assert not transport.accepted
    assert transport.closed == (CLOSE_FEED_START_FAILED, "feed_start_failed")


@pytest.mark.anyio
async def test_successful_connect_then_disconnect_tears_down_after_grace() -> None:
    hub = FeedHub(grace_period_seconds=0.05)
    update = NormalizedCandleUpdate(datetime(2026, 9, 17, tzinfo=UTC), _ohlcv(), False)
    handle_box: dict = {}

    def starter(key: StreamFeedKey, sink: FeedSink) -> FeedWorkerHandle:
        sink.publish_candle(update)
        handle = _FakeWorkerHandle()
        handle_box["handle"] = handle
        return handle

    async def build_starter(claims, loop):
        return starter, "binance_testnet"

    transport = _FakeTransport(disconnect_after=1)
    await _run_with_timeout(
        run_stream_session(
            transport,
            StreamSessionParams(ticket=_issue_ticket()),
            ticket_secret=TICKET_SECRET,
            used_tickets=UsedTicketStore(),
            hub=hub,
            build_starter=build_starter,
            heartbeat_interval_seconds=0.05,
        )
    )

    assert transport.accepted
    assert transport.closed is None
    assert transport.sent[0]["type"] == "stream_state"
    assert transport.sent[0]["resume"] == "fresh"
    assert transport.sent[1]["event_type"] == "provisional_update"
    assert transport.sent[1]["workspace_id"] == str(WORKSPACE_ID)

    # RT-05: last subscriber gone -> upstream torn down after grace period.
    await asyncio.sleep(0.15)
    assert handle_box["handle"].stopped
    assert hub.active_feed_count() == 0


@pytest.mark.anyio
async def test_live_events_are_forwarded_and_heartbeat_fires_when_idle() -> None:
    hub = FeedHub(grace_period_seconds=0.01)
    sink_box: dict = {}

    def starter(key: StreamFeedKey, sink: FeedSink) -> FeedWorkerHandle:
        sink_box["sink"] = sink
        return _FakeWorkerHandle()

    async def build_starter(claims, loop):
        return starter, "binance_testnet"

    transport = _FakeTransport()
    session_task = asyncio.create_task(
        run_stream_session(
            transport,
            StreamSessionParams(ticket=_issue_ticket()),
            ticket_secret=TICKET_SECRET,
            used_tickets=UsedTicketStore(),
            hub=hub,
            build_starter=build_starter,
            heartbeat_interval_seconds=0.05,
        )
    )
    await asyncio.sleep(0.02)  # let it connect and reach the pump loop
    assert transport.accepted

    sink_box["sink"].publish_candle(
        NormalizedCandleUpdate(datetime(2026, 9, 17, tzinfo=UTC), _ohlcv(), False)
    )
    await asyncio.sleep(0.02)
    assert transport.sent[-1]["event_type"] == "provisional_update"

    # No further events -- a heartbeat should fire on its own.
    await asyncio.sleep(0.12)
    assert any(message.get("event_type") == "heartbeat" for message in transport.sent)
    heartbeat = next(m for m in transport.sent if m.get("event_type") == "heartbeat")
    assert heartbeat["sequence"] == transport.sent[1]["sequence"]  # unchanged, per module docstring

    transport.trigger_disconnect()  # wake the watcher's pending receive() to end the session
    await _run_with_timeout(session_task)
    await asyncio.sleep(0.03)  # let the grace-period teardown task actually run (RT-05)
    assert hub.active_feed_count() == 0


def _ohlcv() -> OHLCV:
    return OHLCV(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("1"))
