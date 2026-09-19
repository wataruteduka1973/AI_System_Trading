"""In-memory pub-sub and lifecycle management for real-time market-data feeds.

See docs/design/modules/realtime-market-data-stream.md sections 3 and 5 for
the feed lifecycle and event shape this module implements, and
docs/plans/realtime-market-data-stream.md (work unit 3) for scope.

A "feed" is keyed by (exchange, symbol, timeframe) and is intentionally
Workspace-independent: price data itself carries no Workspace-specific
information, so a single upstream exchange connection is shared by every
subscriber across every Workspace watching the same feed (RT-04). The
`workspace_id` field described for the browser-facing event JSON (module
design section 5) is therefore NOT part of `CandleStreamEvent` here -- it is
stamped onto the outgoing WebSocket message per-connection by the WS
termination layer (work unit 5), which already knows the subscribing
client's own workspace_id from its stream ticket.

This module never opens an exchange connection itself. It only manages feed
bookkeeping (sequence numbers, ring buffer, subscriber fan-out, lazy
start/grace-period teardown) and expects a `FeedStarter` callback -- supplied
by an exchange adapter such as `app.market_data.infrastructure.oanda_stream`
-- to actually open the upstream connection and report updates back via the
`FeedSink` it is given.

Concurrency: every public method is meant to be called from the single
asyncio event loop that owns a given `FeedHub` instance (an adapter's own
background thread never calls them directly -- it only calls `FeedSink`
methods via `loop.call_soon_threadsafe`, see `oanda_stream.OandaFeedWorker`).
No explicit lock is used: each method that touches shared feed state does so
without any `await` in between reading and writing it, so it always runs to
completion atomically before the event loop can run another callback.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_GRACE_PERIOD_SECONDS = 30.0
DEFAULT_RING_BUFFER_SIZE = 200
DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE = 1000
"""Bounds each subscriber's per-connection delivery queue (see
`FeedHub.subscribe`/`_append_and_fanout`). A subscriber this far behind is
disconnected instead of silently losing events -- see `SubscriberOverflow`
docstring for why."""

EventType = Literal["provisional_update", "candle_finalized", "heartbeat", "gap_notice"]
FeedState = Literal["starting", "connected", "delayed", "disconnected"]


@dataclass(frozen=True)
class StreamFeedKey:
    """Identifies a shared upstream feed. Deliberately excludes workspace_id
    -- see module docstring."""

    exchange: str
    symbol: str
    timeframe: str


@dataclass(frozen=True)
class OHLCV:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class NormalizedCandleUpdate:
    """What an exchange adapter reports for one candle update. FeedHub
    assigns event_id/sequence and wraps it into a CandleStreamEvent."""

    open_time: datetime
    ohlcv: OHLCV
    is_final: bool


class SubscriberOverflow:
    """Sentinel pushed into a subscriber's queue in place of an ordinary
    event once that subscriber has fallen far enough behind to fill its
    bounded queue (see `_append_and_fanout`). `_pump` (stream_session.py)
    treats receiving this as a forced-disconnect signal -- it is exported
    (not underscore-prefixed) for that cross-module check.

    This project's mid-session wire protocol never checks for a `sequence`
    gap -- gaps are only detected at reconnect time via
    `stream_protocol.decide_resume` (comparing the reconnecting client's
    remembered `last_sequence` against the feed's ring buffer). Silently
    dropping the oldest queued event for a slow subscriber would therefore
    create a mid-session gap no client-side code currently notices. Forcing
    a disconnect instead routes the same subscriber through the existing,
    already-tested reconnect + REST gap-fill path (RT-08) rather than
    inventing a second, undetectable kind of gap.
    """


SUBSCRIBER_OVERFLOW = SubscriberOverflow()


@dataclass(frozen=True)
class CandleStreamEvent:
    """The internal, workspace-independent representation of one feed event.
    See docs/design/modules/realtime-market-data-stream.md section 5 for the
    corresponding browser-facing JSON shape (which adds workspace_id).
    `open_time`/`ohlcv` are None for heartbeat/gap_notice events; `quality`
    is None for the same. `reason_code` is only set for gap_notice (a safe,
    stable code -- never a raw exception message or credential detail, per
    design doc section 8)."""

    event_id: str
    exchange: str
    symbol: str
    timeframe: str
    sequence: int
    event_type: EventType
    open_time: datetime | None
    ohlcv: OHLCV | None
    source: str
    quality: Literal["provisional", "final"] | None
    reason_code: str | None = None


class FeedSink(Protocol):
    """Given to a `FeedStarter` so an exchange adapter can report normalized
    updates and failures back into the feed. Implementations are called by
    FeedHub itself (via `loop.call_soon_threadsafe` from the adapter's own
    thread) -- adapters must never touch FeedHub internals directly."""

    def publish_candle(self, update: NormalizedCandleUpdate) -> None: ...

    def report_failure(self, code: str, *, state: FeedState = "disconnected") -> None: ...


class FeedWorkerHandle(Protocol):
    """Returned by a FeedStarter. Lets FeedHub stop the upstream connection
    once a feed has no subscribers left after its grace period."""

    def stop(self) -> None: ...


FeedStarter = Callable[[StreamFeedKey, FeedSink], FeedWorkerHandle]
"""Starts an upstream exchange connection for `key` and returns a handle to
stop it later. Must not block -- a typical implementation spawns a
background thread (see `oanda_stream.OandaFeedWorker`) and returns
immediately."""


@dataclass
class _Feed:
    key: StreamFeedKey
    source: str
    ring_buffer: deque[CandleStreamEvent]
    worker: FeedWorkerHandle | None = None
    state: FeedState = "starting"
    sequence: int = -1
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    subscribers: dict[int, asyncio.Queue[CandleStreamEvent | SubscriberOverflow]] = field(
        default_factory=dict
    )
    teardown_task: asyncio.Task[None] | None = None


class FeedSubscription:
    """Handle returned by `FeedHub.subscribe`. `buffered_events` are the
    events already in the feed's ring buffer at subscribe time (empty for a
    freshly-started feed); `queue` receives every event published after
    that, in order. `feed_started_at` lets a reconnecting client detect
    whether the feed it last saw is still the same one (module design
    section 7 -- the actual reconnect/gap-fill protocol is work unit 6)."""

    def __init__(
        self,
        hub: FeedHub,
        key: StreamFeedKey,
        subscriber_id: int,
        queue: asyncio.Queue[CandleStreamEvent | SubscriberOverflow],
        buffered_events: list[CandleStreamEvent],
        feed_started_at: datetime,
    ) -> None:
        self.key = key
        self.queue = queue
        self.buffered_events = buffered_events
        self.feed_started_at = feed_started_at
        self._hub = hub
        self._subscriber_id = subscriber_id
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._hub._unsubscribe(self.key, self._subscriber_id)


class _FeedSink:
    def __init__(self, hub: FeedHub, key: StreamFeedKey) -> None:
        self._hub = hub
        self._key = key

    def publish_candle(self, update: NormalizedCandleUpdate) -> None:
        self._hub._publish_candle(self._key, update)

    def report_failure(self, code: str, *, state: FeedState = "disconnected") -> None:
        self._hub._report_failure(self._key, code, state=state)


class FeedHub:
    """In-memory pub-sub keyed by (exchange, symbol, timeframe). See module
    docstring and docs/design/modules/realtime-market-data-stream.md
    section 3. Each subscriber's delivery queue is bounded
    (`subscriber_queue_maxsize`); a subscriber that falls far enough behind
    to fill it is force-disconnected via `SubscriberOverflow` rather than
    silently losing events or raising out of the publishing adapter's call
    stack -- see that class's docstring."""

    def __init__(
        self,
        *,
        grace_period_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS,
        ring_buffer_size: int = DEFAULT_RING_BUFFER_SIZE,
        subscriber_queue_maxsize: int = DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE,
    ) -> None:
        if grace_period_seconds < 0:
            raise ValueError("grace_period_seconds must not be negative")
        if ring_buffer_size < 1:
            raise ValueError("ring_buffer_size must be at least 1")
        if subscriber_queue_maxsize < 1:
            raise ValueError("subscriber_queue_maxsize must be at least 1")
        self._grace_period_seconds = grace_period_seconds
        self._ring_buffer_size = ring_buffer_size
        self._subscriber_queue_maxsize = subscriber_queue_maxsize
        self._feeds: dict[StreamFeedKey, _Feed] = {}
        self._subscriber_ids = itertools.count(1)

    async def subscribe(
        self, key: StreamFeedKey, *, source: str, starter: FeedStarter
    ) -> FeedSubscription:
        """Attach a new subscriber to `key`, starting its upstream connection
        via `starter` if it is not already running (RT-04), or reusing one
        still inside its grace-period teardown window (RT-06). `source` is
        only used when a new feed is created; it is ignored for a feed that
        already exists (its source was fixed by whichever subscriber created
        it)."""
        feed = self._feeds.get(key)
        if feed is None:
            feed = self._start_feed(key, source, starter)
        elif feed.teardown_task is not None:
            feed.teardown_task.cancel()
            feed.teardown_task = None
        subscriber_id = next(self._subscriber_ids)
        queue: asyncio.Queue[CandleStreamEvent | SubscriberOverflow] = asyncio.Queue(
            maxsize=self._subscriber_queue_maxsize
        )
        feed.subscribers[subscriber_id] = queue
        return FeedSubscription(
            self, key, subscriber_id, queue, list(feed.ring_buffer), feed.started_at
        )

    def feed_state(self, key: StreamFeedKey) -> FeedState | None:
        feed = self._feeds.get(key)
        return feed.state if feed is not None else None

    def active_feed_count(self) -> int:
        return len(self._feeds)

    def _start_feed(self, key: StreamFeedKey, source: str, starter: FeedStarter) -> _Feed:
        # The feed is created and registered *before* `starter` runs, so that
        # any event published synchronously during startup (or from a
        # background thread scheduled via call_soon_threadsafe before this
        # function even returns) is not silently dropped by `_publish_candle`
        # / `_report_failure` finding no registered feed yet.
        feed = _Feed(key=key, source=source, ring_buffer=deque(maxlen=self._ring_buffer_size))
        self._feeds[key] = feed
        try:
            feed.worker = starter(key, _FeedSink(self, key))
        except Exception:
            del self._feeds[key]
            logger.warning(
                "feed_start_failed",
                exchange=key.exchange,
                symbol=key.symbol,
                timeframe=key.timeframe,
            )
            raise
        logger.info(
            "feed_started", exchange=key.exchange, symbol=key.symbol, timeframe=key.timeframe
        )
        return feed

    def _publish_candle(self, key: StreamFeedKey, update: NormalizedCandleUpdate) -> None:
        feed = self._feeds.get(key)
        if feed is None:
            return  # feed was already torn down; drop the late event
        if feed.state != "connected":
            logger.info(
                "feed_recovered",
                exchange=key.exchange,
                symbol=key.symbol,
                timeframe=key.timeframe,
                previous_state=feed.state,
            )
        feed.sequence += 1
        feed.state = "connected"
        event = CandleStreamEvent(
            event_id=uuid4().hex,
            exchange=key.exchange,
            symbol=key.symbol,
            timeframe=key.timeframe,
            sequence=feed.sequence,
            event_type="candle_finalized" if update.is_final else "provisional_update",
            open_time=update.open_time,
            ohlcv=update.ohlcv,
            source=feed.source,
            quality="final" if update.is_final else "provisional",
        )
        self._append_and_fanout(feed, event)

    def _report_failure(self, key: StreamFeedKey, code: str, *, state: FeedState) -> None:
        feed = self._feeds.get(key)
        if feed is None:
            return
        logger.warning(
            "feed_failure",
            exchange=key.exchange,
            symbol=key.symbol,
            timeframe=key.timeframe,
            code=code,
            previous_state=feed.state,
            new_state=state,
        )
        feed.state = state
        feed.sequence += 1
        event = CandleStreamEvent(
            event_id=uuid4().hex,
            exchange=key.exchange,
            symbol=key.symbol,
            timeframe=key.timeframe,
            sequence=feed.sequence,
            event_type="gap_notice",
            open_time=None,
            ohlcv=None,
            source=feed.source,
            quality=None,
            reason_code=code,
        )
        self._append_and_fanout(feed, event)

    def _append_and_fanout(self, feed: _Feed, event: CandleStreamEvent) -> None:
        feed.ring_buffer.append(event)
        for subscriber_id, queue in list(feed.subscribers.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "feed_subscriber_queue_overflow",
                    exchange=feed.key.exchange,
                    symbol=feed.key.symbol,
                    timeframe=feed.key.timeframe,
                    subscriber_id=subscriber_id,
                    queue_maxsize=self._subscriber_queue_maxsize,
                )
                self._force_disconnect_subscriber(queue)

    def _force_disconnect_subscriber(
        self, queue: asyncio.Queue[CandleStreamEvent | SubscriberOverflow]
    ) -> None:
        """Drain `queue` and push `SUBSCRIBER_OVERFLOW` in its place. This
        method itself never blocks or raises: draining first guarantees the
        immediately-following `put_nowait` has room, so this stays safe to
        call synchronously from `_append_and_fanout` (see module docstring
        on why no method here may `await`)."""
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(SUBSCRIBER_OVERFLOW)

    async def _unsubscribe(self, key: StreamFeedKey, subscriber_id: int) -> None:
        feed = self._feeds.get(key)
        if feed is None:
            return
        feed.subscribers.pop(subscriber_id, None)
        if not feed.subscribers and feed.teardown_task is None:
            feed.teardown_task = asyncio.create_task(
                self._teardown_after_grace(key), name=f"feed-teardown-{key.exchange}-{key.symbol}"
            )

    async def _teardown_after_grace(self, key: StreamFeedKey) -> None:
        try:
            await asyncio.sleep(self._grace_period_seconds)
        except asyncio.CancelledError:
            return
        feed = self._feeds.get(key)
        if feed is None or feed.subscribers:
            return  # resubscribed during the grace period (RT-06), or already gone
        del self._feeds[key]
        logger.info(
            "feed_torn_down", exchange=key.exchange, symbol=key.symbol, timeframe=key.timeframe
        )
        if feed.worker is not None:
            feed.worker.stop()
