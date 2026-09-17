"""Tests for the in-memory feed pub-sub/lifecycle hub. See
docs/design/modules/realtime-market-data-stream.md section 3 and
docs/plans/realtime-market-data-stream.md (work unit 3, RT-04/05/06)."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    FeedHub,
    NormalizedCandleUpdate,
    StreamFeedKey,
)

KEY = StreamFeedKey("oanda", "USD_JPY", "1m")


class _FakeWorkerHandle:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _update(price: str, *, is_final: bool = False) -> NormalizedCandleUpdate:
    value = Decimal(price)
    ohlcv = OHLCV(value, value, value, value, Decimal(1))
    return NormalizedCandleUpdate(datetime(2026, 9, 17, 1, 0, tzinfo=UTC), ohlcv, is_final)


def test_first_subscriber_starts_the_upstream_connection_exactly_once() -> None:
    """RT-04: only the first subscriber to a feed key starts an upstream
    connection; a synchronous publish made during startup is not lost."""
    starter_calls: list[StreamFeedKey] = []

    def starter(key, sink):
        starter_calls.append(key)
        sink.publish_candle(_update("150.10"))
        return _FakeWorkerHandle()

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2)
        sub = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        assert len(starter_calls) == 1
        assert len(sub.buffered_events) == 1
        assert sub.buffered_events[0].sequence == 0
        await sub.close()

    asyncio.run(scenario())


def test_second_subscriber_shares_the_existing_feed() -> None:
    """RT-04: a second subscriber to the same (exchange, symbol, timeframe)
    attaches to the already-running feed instead of starting a new one, and
    both receive the same subsequent event."""
    starter_calls: list[StreamFeedKey] = []

    def starter(key, sink):
        starter_calls.append(key)
        return _FakeWorkerHandle()

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2)
        sub1 = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        sub2 = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        assert len(starter_calls) == 1

        hub._publish_candle(KEY, _update("150.20", is_final=True))
        event1 = sub1.queue.get_nowait()
        event2 = sub2.queue.get_nowait()
        assert event1.event_id == event2.event_id
        assert event1.event_type == "candle_finalized"

        await sub1.close()
        await sub2.close()

    asyncio.run(scenario())


def test_feed_tears_down_after_grace_period_once_all_subscribers_leave() -> None:
    """RT-05: the upstream connection is stopped only after the last
    subscriber has been gone for the full grace period, not immediately."""
    handle = _FakeWorkerHandle()

    def starter(key, sink):
        return handle

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2)
        sub = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        await sub.close()

        assert hub.active_feed_count() == 1
        assert handle.stopped is False

        await asyncio.sleep(0.35)
        assert hub.active_feed_count() == 0
        assert handle.stopped is True

    asyncio.run(scenario())


def test_resubscribe_within_grace_period_reuses_the_feed() -> None:
    """RT-06: resubscribing before the grace period elapses cancels the
    pending teardown and reuses the existing feed instead of starting a new
    upstream connection."""
    starter_calls: list[StreamFeedKey] = []
    handle = _FakeWorkerHandle()

    def starter(key, sink):
        starter_calls.append(key)
        return handle

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2)
        sub1 = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        await sub1.close()

        sub2 = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        assert len(starter_calls) == 1  # no second upstream connection was started

        await asyncio.sleep(0.35)  # past the original (cancelled) grace deadline
        assert hub.active_feed_count() == 1
        assert handle.stopped is False

        await sub2.close()
        await asyncio.sleep(0.35)
        assert hub.active_feed_count() == 0
        assert handle.stopped is True

    asyncio.run(scenario())


def test_late_subscriber_receives_ring_buffer_backlog() -> None:
    """A subscriber joining after events were already published still gets
    them via the ring buffer snapshot, ahead of anything published later
    (groundwork for RT-07's reconnect replay, implemented in work unit 6)."""

    def starter(key, sink):
        return _FakeWorkerHandle()

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2, ring_buffer_size=10)
        sub1 = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        hub._publish_candle(KEY, _update("150.10"))
        hub._publish_candle(KEY, _update("150.20"))

        sub2 = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        assert [event.sequence for event in sub2.buffered_events] == [0, 1]

        await sub1.close()
        await sub2.close()

    asyncio.run(scenario())


def test_starter_failure_leaves_no_feed_registered() -> None:
    """If starting the upstream connection raises, the failed feed must not
    be left half-registered -- a later subscribe for the same key should be
    able to try again cleanly."""

    def bad_starter(key, sink):
        raise RuntimeError("boom")

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2)
        try:
            await hub.subscribe(KEY, source="oanda_practice", starter=bad_starter)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the starter's exception to propagate")
        assert hub.active_feed_count() == 0

        def recovery_starter(key, sink):
            return _FakeWorkerHandle()

        sub = await hub.subscribe(KEY, source="oanda_practice", starter=recovery_starter)
        assert hub.active_feed_count() == 1
        await sub.close()

    asyncio.run(scenario())


def test_report_failure_publishes_gap_notice_and_updates_feed_state() -> None:
    """RT-11: a reported upstream failure becomes a gap_notice event (no
    OHLCV, only a safe reason_code) and moves the feed's own state."""

    def starter(key, sink):
        sink.report_failure("oanda_unreachable")
        return _FakeWorkerHandle()

    async def scenario() -> None:
        hub = FeedHub(grace_period_seconds=0.2)
        sub = await hub.subscribe(KEY, source="oanda_practice", starter=starter)
        assert len(sub.buffered_events) == 1
        event = sub.buffered_events[0]
        assert event.event_type == "gap_notice"
        assert event.reason_code == "oanda_unreachable"
        assert event.ohlcv is None
        assert event.open_time is None
        assert hub.feed_state(KEY) == "disconnected"
        await sub.close()

    asyncio.run(scenario())
