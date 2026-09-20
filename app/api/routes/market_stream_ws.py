"""WebSocket termination for the realtime market-data stream:
`WS /ws/v1/market-stream?ticket=<ticket>`.

See docs/design/modules/realtime-market-data-stream.md sections 3/4/5/7 and
docs/plans/realtime-market-data-stream.md (work units 5 and 6).

This module is deliberately thin. Ticket verification
(app.market_data.infrastructure.stream_tickets), feed lifecycle/ref-counting
/grace-period teardown (app.market_data.infrastructure.candle_stream.FeedHub,
already implemented in work unit 3), the reconnect/resume decision and wire-
protocol encoding (app.market_data.infrastructure.stream_protocol, work unit
6), and the whole connection orchestration (app.market_data.infrastructure.
stream_session.run_stream_session, work units 5/6) are all pure, directly-
tested modules -- see their own docstrings and tests/test_stream_session.py.
This file only:

1. Resolves the real (DB-backed) exchange-connection credentials for a
   verified ticket's (workspace_id, exchange) and builds the matching
   FeedStarter (OANDA or Binance) -- the `BuildStarter` callback
   `run_stream_session` calls.
2. Adapts a real `fastapi.WebSocket` into `stream_session.Transport`.
3. Wires both into `run_stream_session` behind `@router.websocket(...)`.

NOT VERIFIED locally: fastapi/starlette ultimately depend on pydantic-core,
a compiled Rust extension with no Linux build available in this sandbox
(see docs/plans/realtime-market-data-stream.md work unit 5/6 verification
notes), so this file itself could not be genuinely imported or executed
here, unlike every module it wires together. It is written to match the
exact conventions of the already-shipped, equally-unverified-locally
`create_market_stream_ticket` endpoint in app/api/routes/market_data.py
(work unit 2), and is covered by tests/test_market_stream_ws.py for CI.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from functools import cache, lru_cache
from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.market_data.application.spread_tracking import record_spread_observation
from app.market_data.infrastructure.binance_stream import make_binance_feed_starter
from app.market_data.infrastructure.candle_stream import FeedHub, FeedStarter
from app.market_data.infrastructure.oanda_stream import OandaPriceTick, make_oanda_feed_starter
from app.market_data.infrastructure.stream_connection_access import (
    StreamConnectionCredentials,
    resolve_stream_connection_credentials,
)
from app.market_data.infrastructure.stream_session import (
    Disconnected,
    StreamAccessDenied,
    StreamSessionParams,
    run_stream_session,
)
from app.market_data.infrastructure.stream_tickets import (
    StreamTicketClaims,
    get_default_used_ticket_store,
)
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.services.market_data import MarketDataAccessError
from app.services.secrets import get_secret_store

router = APIRouter()
logger = structlog.get_logger("app.market_stream")

_SOURCE_BY_EXCHANGE = {"oanda": "oanda_practice", "binance": "binance_testnet"}


@lru_cache
def get_default_feed_hub() -> FeedHub:
    settings = get_settings()
    return FeedHub(grace_period_seconds=settings.market_stream_grace_period_seconds)


class _WebSocketTransport:
    """Adapts a real fastapi.WebSocket to stream_session.Transport. See
    module docstring for why this class itself is NOT VERIFIED locally."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def accept(self) -> None:
        await self._websocket.accept()

    async def send(self, message: dict[str, object]) -> None:
        await self._websocket.send_json(message)

    async def receive(self) -> None:
        try:
            await self._websocket.receive_text()
        except WebSocketDisconnect as exc:
            raise Disconnected from exc

    async def close(self, *, code: int, reason: str) -> None:
        await self._websocket.close(code=code, reason=reason)


def _resolve_credentials_sync(workspace_id: UUID, exchange: str) -> StreamConnectionCredentials:
    with SessionLocal() as db:
        return resolve_stream_connection_credentials(
            db, get_secret_store(), workspace_id=workspace_id, exchange=exchange
        )


@cache
def _resolve_oanda_instrument_id(symbol: str) -> UUID | None:
    # Cached for the process lifetime, matching get_default_feed_hub()'s own caching:
    # (exchange, symbol) -> instrument_id is effectively static reference data. A `None`
    # result (instrument not yet known) is cached too, so adding an instrument requires
    # a process restart to be picked up here -- acceptable for this project's scale.
    with SessionLocal() as db:
        return db.scalar(
            select(Instrument.id)
            .join(Exchange, Instrument.exchange_id == Exchange.id)
            .where(Exchange.code == "oanda", Instrument.symbol == symbol)
        )


def _record_oanda_spread(symbol: str, tick: OandaPriceTick) -> None:
    instrument_id = _resolve_oanda_instrument_id(symbol)
    if instrument_id is None:
        return
    with SessionLocal() as db:
        record_spread_observation(
            db,
            instrument_id,
            bid=tick.bid,
            ask=tick.ask,
            observed_at=tick.time,
            source="oanda_practice",
        )


def _build_starter_for(
    credentials: StreamConnectionCredentials, loop: asyncio.AbstractEventLoop
) -> FeedStarter:
    if credentials.exchange == "oanda":
        assert credentials.token is not None
        assert credentials.account_id is not None
        return make_oanda_feed_starter(
            base_url=credentials.base_url,
            token=credentials.token,
            account_id=credentials.account_id,
            loop=loop,
            record_spread=_record_oanda_spread,
        )
    assert credentials.api_key is not None
    assert credentials.secret_key is not None
    return make_binance_feed_starter(base_url=credentials.base_url)


async def _build_starter(
    claims: StreamTicketClaims, loop: asyncio.AbstractEventLoop
) -> tuple[FeedStarter, str]:
    try:
        credentials = await asyncio.to_thread(
            _resolve_credentials_sync, claims.workspace_id, claims.exchange
        )
    except MarketDataAccessError as exc:
        raise StreamAccessDenied(exc.code) from exc
    starter = _build_starter_for(credentials, loop)
    source = _SOURCE_BY_EXCHANGE[claims.exchange]
    return starter, source


@router.websocket("/ws/v1/market-stream")
async def market_stream_ws(
    websocket: WebSocket,
    ticket: str,
    resume_last_sequence: int | None = None,
    resume_feed_started_at: datetime | None = None,
) -> None:
    settings = get_settings()
    ticket_secret = settings.market_stream_ticket_secret
    if ticket_secret is None or not ticket_secret.get_secret_value():
        await websocket.close(code=1013, reason="ticket_signing_unavailable")
        return
    try:
        await run_stream_session(
            _WebSocketTransport(websocket),
            StreamSessionParams(ticket, resume_last_sequence, resume_feed_started_at),
            ticket_secret=ticket_secret.get_secret_value(),
            used_tickets=get_default_used_ticket_store(),
            hub=get_default_feed_hub(),
            build_starter=_build_starter,
            heartbeat_interval_seconds=settings.market_stream_heartbeat_interval_seconds,
        )
    except WebSocketDisconnect:
        # An ordinary disconnect race (e.g. the client closed the tab just
        # as a send was in flight) -- not an error worth logging.
        pass
    except Exception:  # pragma: no cover - defensive: never crash silently
        # Never log the ticket itself (a bearer credential) -- only that
        # something unexpected happened, mirroring app/main.py's own
        # request-logging redaction policy.
        logger.exception("market_stream_session_failed")
        # A closed/never-accepted WebSocket may itself raise on close();
        # suppressed so that failure never masks the exception just logged.
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="internal_error")
