"""Framework-independent orchestration for one browser WebSocket market-
stream connection: ticket verification, credential/starter resolution,
FeedHub subscribe/unsubscribe (ref-counting and grace-period teardown are
already implemented by FeedHub itself -- work unit 3), the reconnect/resume
decision, and the heartbeat/event send loop.

See docs/plans/realtime-market-data-stream.md (work units 5 and 6) and
docs/design/modules/realtime-market-data-stream.md sections 3/4/5/7.

Deliberately takes a small `Transport` protocol instead of a real
`fastapi.WebSocket`, so the entire connection lifecycle -- including timing-
sensitive behavior like the heartbeat -- can be exercised with plain
asyncio and no fastapi/starlette/pydantic dependency.
app/api/routes/market_stream_ws.py is the thin FastAPI adapter that wraps a
real WebSocket into `Transport` (and resolves real DB credentials into a
`BuildStarter`) and calls `run_stream_session`; see that module's docstring
for why the adapter itself could not be genuinely executed in this sandbox
and is therefore NOT VERIFIED pending CI, unlike this module.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.market_data.infrastructure.candle_stream import FeedHub, FeedStarter, StreamFeedKey
from app.market_data.infrastructure.stream_protocol import (
    ResumeRequest,
    decide_resume,
    encode_event,
    encode_heartbeat,
    encode_stream_state,
)
from app.market_data.infrastructure.stream_tickets import (
    StreamTicketClaims,
    StreamTicketError,
    UsedTicketStore,
    verify_and_consume_ticket,
)

# WS close codes (4000-4999 is the private-use range per RFC 6455 7.4.2).
# `reason` always carries one of this module's own safe, stable string
# codes (ticket error codes from stream_tickets.py, or a
# StreamAccessDenied.code) -- never a raw exception message or credential
# detail, matching the rest of this feature's error-handling convention.
CLOSE_TICKET_REJECTED = 4401
CLOSE_ACCESS_DENIED = 4403
CLOSE_FEED_START_FAILED = 1011


class Disconnected(Exception):
    """Raised by Transport.receive() once the client has disconnected."""


class Transport(Protocol):
    async def accept(self) -> None: ...

    async def send(self, message: dict[str, object]) -> None: ...

    async def receive(self) -> None:
        """Waits for the next client -> server message (content is not
        used by this protocol -- the browser client is not expected to
        send anything meaningful) or raises Disconnected once the
        connection has closed."""
        ...

    async def close(self, *, code: int, reason: str) -> None: ...


class StreamAccessDenied(Exception):
    """Raised by a BuildStarter to deny a connection with a safe, stable
    reason code (e.g. one of MarketDataAccessError's codes)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


BuildStarter = Callable[
    [StreamTicketClaims, asyncio.AbstractEventLoop], Awaitable[tuple[FeedStarter, str]]
]
"""Given verified ticket claims and the running event loop, resolve
whatever credentials are needed and return (starter, source) -- see
candle_stream.FeedStarter and FeedHub.subscribe's `source`. Raises
StreamAccessDenied to refuse the connection. See
app.api.routes.market_stream_ws for the real (DB-backed) implementation."""


@dataclass(frozen=True)
class StreamSessionParams:
    ticket: str
    resume_last_sequence: int | None = None
    resume_feed_started_at: datetime | None = None


async def run_stream_session(
    transport: Transport,
    params: StreamSessionParams,
    *,
    ticket_secret: str,
    used_tickets: UsedTicketStore,
    hub: FeedHub,
    build_starter: BuildStarter,
    heartbeat_interval_seconds: float,
) -> None:
    try:
        claims = verify_and_consume_ticket(
            ticket_secret, params.ticket, used_tickets=used_tickets
        )
    except StreamTicketError as exc:
        await transport.close(code=CLOSE_TICKET_REJECTED, reason=exc.code)
        return

    loop = asyncio.get_running_loop()
    try:
        starter, source = await build_starter(claims, loop)
    except StreamAccessDenied as exc:
        await transport.close(code=CLOSE_ACCESS_DENIED, reason=exc.code)
        return

    key = StreamFeedKey(claims.exchange, claims.symbol, claims.timeframe)
    try:
        subscription = await hub.subscribe(key, source=source, starter=starter)
    except Exception:
        await transport.close(code=CLOSE_FEED_START_FAILED, reason="feed_start_failed")
        return

    await transport.accept()
    try:
        resume_request = ResumeRequest(
            params.resume_last_sequence, params.resume_feed_started_at
        )
        decision = decide_resume(
            resume_request,
            feed_started_at=subscription.feed_started_at,
            buffered_events=subscription.buffered_events,
        )
        last_sequence = resume_request.last_sequence if resume_request.is_present else -1
        await transport.send(encode_stream_state(subscription.feed_started_at, decision.mode))
        for event in decision.events_to_replay:
            last_sequence = event.sequence
            await transport.send(encode_event(event, claims.workspace_id))
        await _pump(
            transport,
            subscription,
            claims.workspace_id,
            source,
            heartbeat_interval_seconds,
            last_sequence,
        )
    except Disconnected:
        pass
    finally:
        await subscription.close()


async def _pump(
    transport: Transport,
    subscription,
    workspace_id,
    source: str,
    heartbeat_interval_seconds: float,
    last_sequence: int,
) -> None:
    # A watcher task drives Transport.receive() in a loop purely to detect
    # disconnect; the main loop below polls `disconnected` at most once per
    # heartbeat interval, so heartbeat cadence doubles as the
    # disconnect-detection granularity (an intentional MVP simplification --
    # see docs/plans/realtime-market-data-stream.md work unit 5).
    disconnected = asyncio.Event()

    async def _watch_disconnect() -> None:
        try:
            while True:
                await transport.receive()
        except Disconnected:
            pass
        finally:
            disconnected.set()

    watcher = asyncio.create_task(_watch_disconnect())
    try:
        while not disconnected.is_set():
            try:
                event = await asyncio.wait_for(
                    subscription.queue.get(), timeout=heartbeat_interval_seconds
                )
            except TimeoutError:
                if disconnected.is_set():
                    break
                await transport.send(
                    encode_heartbeat(subscription.key, workspace_id, last_sequence, source)
                )
                continue
            if disconnected.is_set():
                break
            last_sequence = event.sequence
            await transport.send(encode_event(event, workspace_id))
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
