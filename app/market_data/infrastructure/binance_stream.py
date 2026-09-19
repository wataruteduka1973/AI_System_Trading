"""Binance adapter: connects `BinanceSocketManager.kline_socket` to the same
FeedHub pub-sub the OANDA adapter uses (work unit 3). See
docs/design/modules/realtime-market-data-stream.md section 6 for the kline
message shape and why no tick-to-candle synthesis is needed here (Binance's
kline stream already delivers provisional/finalized candles natively via
the `x` (is_closed) flag), and docs/plans/realtime-market-data-stream.md
(work unit 4) for scope.

Unlike the OANDA adapter, `BinanceSocketManager` is a native asyncio API --
there is no blocking generator and therefore no need for a dedicated thread
or a `loop.call_soon_threadsafe` bridge. `BinanceFeedWorker` simply runs as
a plain `asyncio.Task` on the same event loop that owns the FeedHub, and
calls `FeedSink` methods directly from that task.

`kline_socket` streams are public Binance market data (no signature, no
API key). `make_binance_feed_starter` still requires and validates a
testnet `base_url` -- the same check `BinanceSpotTestnetClient` applies to
REST calls -- purely as defense in depth for this project's paper/testnet-
only policy: the websocket URL itself is never taken from a caller-supplied
value, it is always the `wss://stream.testnet.binance.vision/` constant
`BinanceSocketManager` resolves internally from `AsyncClient(testnet=True)`
(hardcoded here). Rejecting a non-testnet `base_url` up front means a
misconfigured caller fails loudly and synchronously, rather than only
discovering the mistake later as an async gap_notice.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog
from binance import AsyncClient
from binance.exceptions import BinanceAPIException, BinanceRequestException, ReadLoopClosed
from binance.ws.streams import BinanceSocketManager

from app.exchanges.binance import BinanceSpotTestnetClient, ClientFactory
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    FeedSink,
    FeedState,
    FeedWorkerHandle,
    NormalizedCandleUpdate,
    StreamFeedKey,
)

logger = structlog.get_logger(__name__)

SocketManagerFactory = Callable[[AsyncClient], BinanceSocketManager]

DEFAULT_SILENCE_TIMEOUT_SECONDS = 90.0
"""Binance's kline stream pushes an update roughly every 1-2s whenever the
market is open, even without a trade (see design doc section 6). No message
at all -- not even the library's own `{"e": "error", ...}` notices -- for
this long indicates a hung connection that never errored (the 2026-09-19
soak-test incident: a queue-overflow disconnect that WAS reported never
triggered a reconnect, because none existed; this timeout guards the
separate case where the socket does not even report the failure)."""

INITIAL_RECONNECT_BACKOFF_SECONDS = 1.0
MAX_RECONNECT_BACKOFF_SECONDS = 60.0
_BACKOFF_MULTIPLIER = 2.0
DELAYED_ATTEMPT_THRESHOLD = 3
"""Consecutive failed-to-connect attempts before the feed's reported state
escalates from "delayed" (transient, expected to self-heal) to
"disconnected" (prolonged outage) -- see design doc section 8. Reconnection
itself never stops; only the reported FeedState changes."""


def compute_reconnect_backoff_seconds(
    attempt: int,
    *,
    rng: random.Random,
    initial: float = INITIAL_RECONNECT_BACKOFF_SECONDS,
    cap: float = MAX_RECONNECT_BACKOFF_SECONDS,
) -> float:
    """Equal-jitter exponential backoff: grows `initial * 2**(attempt-1)`
    (capped at `cap`), then returns a value uniformly drawn from the top
    half of that range. Half-fixed/half-random keeps a floor under the
    delay (avoids a hot-loop of near-zero retries) while still
    de-synchronizing repeated attempts (avoids a thundering-herd retry
    pattern against a recovering server) -- this is what the 2026-09-19
    soak-test incident's fixed-interval retry (no backoff at all) lacked,
    hammering a refused connection every ~5 seconds for over 12 minutes."""
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    base = min(initial * (_BACKOFF_MULTIPLIER ** (attempt - 1)), cap)
    return rng.uniform(base / 2, base)


class _StreamFailure(Exception):
    """Raised by `_connect_and_stream` for any failure that should trigger
    a reconnect attempt (with backoff) rather than permanently ending the
    feed. `connected` is True if at least one message was received on this
    attempt before it failed -- used by the caller to decide whether to
    reset the backoff/attempt counter (a connection that worked for a while
    before dropping is not the same kind of problem as one that never
    connects at all)."""

    def __init__(self, code: str, *, connected: bool) -> None:
        super().__init__(code)
        self.code = code
        self.connected = connected


def parse_kline_message(payload: object) -> NormalizedCandleUpdate | None:
    """Parse one `kline_socket` message. Returns None for anything that is
    not a usable kline event -- the library's own `{"e": "error", ...}`
    reconnect/failure notices (see `_is_stream_error`), or a malformed
    payload -- so the caller can skip it without crashing."""
    if not isinstance(payload, dict) or payload.get("e") != "kline":
        return None
    kline = payload.get("k")
    if not isinstance(kline, dict):
        return None
    try:
        open_time = datetime.fromtimestamp(int(kline["t"]) / 1000, tz=UTC)
        ohlcv = OHLCV(
            open=Decimal(str(kline["o"])),
            high=Decimal(str(kline["h"])),
            low=Decimal(str(kline["l"])),
            close=Decimal(str(kline["c"])),
            volume=Decimal(str(kline["v"])),
        )
        is_final = bool(kline["x"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    if min(ohlcv.open, ohlcv.high, ohlcv.low, ohlcv.close) <= 0:
        return None
    return NormalizedCandleUpdate(open_time=open_time, ohlcv=ohlcv, is_final=is_final)


def _is_stream_error(payload: object) -> bool:
    """True for the `{"e": "error", "type": ..., "m": ...}` notices
    `ReconnectingWebsocket._propagate_error` injects into the same message
    queue `recv()` reads from (connection drops, reconnect-limit exceeded,
    queue overflow). Never surfaced verbatim -- see `_connect_and_stream`."""
    return isinstance(payload, dict) and payload.get("e") == "error"


class _TaskWorkerHandle:
    """FeedWorkerHandle for a Binance feed. Cancelling the task is enough to
    tear the connection down -- the `async with` block's `__aexit__` runs
    during `CancelledError` unwinding and closes the socket."""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task

    def stop(self) -> None:
        self._task.cancel()


class BinanceFeedWorker:
    """Runs `BinanceSocketManager.kline_socket` connections as a plain
    `asyncio.Task` on the caller's own event loop and reports normalized
    candle updates (and failures) into a FeedHub feed via `sink`. See
    module docstring for why this needs no thread, unlike `OandaFeedWorker`.

    Reconnects automatically (exponential backoff + jitter, see
    `compute_reconnect_backoff_seconds`) on any failure -- including a
    watchdog-detected silent hang (`DEFAULT_SILENCE_TIMEOUT_SECONDS`) --
    instead of ending the feed permanently. Only `handle.stop()`
    (`asyncio.CancelledError`) stops it for good. This closes the gap the
    2026-09-19 soak test exposed: a queue-overflow disconnect was reported
    correctly as a gap_notice, but nothing ever retried, leaving the feed
    silently stuck in a disconnected state for hours until the whole API
    process was restarted.
    """

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        sink: FeedSink,
        client_factory: ClientFactory = AsyncClient.create,
        socket_manager_factory: SocketManagerFactory = BinanceSocketManager,
        silence_timeout_seconds: float = DEFAULT_SILENCE_TIMEOUT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        self._symbol = symbol
        self._timeframe = timeframe
        self._sink = sink
        self._client_factory = client_factory
        self._socket_manager_factory = socket_manager_factory
        self._silence_timeout_seconds = silence_timeout_seconds
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()

    def start(self) -> FeedWorkerHandle:
        task = asyncio.create_task(
            self._run(), name=f"binance-stream-{self._symbol}-{self._timeframe}"
        )
        return _TaskWorkerHandle(task)

    async def _run(self) -> None:
        """Outer reconnect loop. `_connect_and_stream` runs one connection
        attempt and either raises `_StreamFailure` (retry after backoff) or
        propagates `CancelledError` (feed teardown -- the only way this
        loop ends)."""
        attempt = 0
        while True:
            try:
                await self._connect_and_stream()
                return  # pragma: no cover - _connect_and_stream only exits via an exception
            except asyncio.CancelledError:
                raise
            except _StreamFailure as exc:
                if exc.connected:
                    # Got real data before this attempt failed -- treat the
                    # next retry as a fresh problem, not a continuation of a
                    # persistent outage.
                    attempt = 0
                attempt += 1
                state: FeedState = (
                    "delayed" if attempt < DELAYED_ATTEMPT_THRESHOLD else "disconnected"
                )
                self._sink.report_failure(exc.code, state=state)
                delay = compute_reconnect_backoff_seconds(attempt, rng=self._rng)
                logger.warning(
                    "binance_stream_reconnect_scheduled",
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    code=exc.code,
                    attempt=attempt,
                    backoff_seconds=round(delay, 2),
                )
                await self._sleep(delay)

    async def _connect_and_stream(self) -> None:
        """One connection attempt: connect, then read messages until
        something goes wrong. Every failure mode raises `_StreamFailure`
        (never reports-and-returns) so `_run` can retry it."""
        client: AsyncClient | None = None
        received_any = False
        try:
            try:
                client = await self._client_factory(testnet=True)
            except (BinanceAPIException, BinanceRequestException, TimeoutError, OSError) as exc:
                raise _StreamFailure("binance_unreachable", connected=False) from exc
            socket_manager = self._socket_manager_factory(client)
            socket = socket_manager.kline_socket(self._symbol, interval=self._timeframe)
            async with socket as stream:
                logger.info(
                    "binance_stream_connected", symbol=self._symbol, timeframe=self._timeframe
                )
                while True:
                    try:
                        message = await asyncio.wait_for(
                            stream.recv(), timeout=self._silence_timeout_seconds
                        )
                    except TimeoutError as exc:
                        # Watchdog: no message at all (not even the
                        # library's own error notice) within the timeout --
                        # see DEFAULT_SILENCE_TIMEOUT_SECONDS.
                        raise _StreamFailure(
                            "binance_stream_silent", connected=received_any
                        ) from exc
                    received_any = True
                    update = parse_kline_message(message)
                    if update is not None:
                        self._sink.publish_candle(update)
                    elif _is_stream_error(message):
                        raise _StreamFailure("binance_stream_disconnected", connected=received_any)
        except asyncio.CancelledError:
            raise
        except _StreamFailure:
            raise
        except ReadLoopClosed as exc:
            raise _StreamFailure("binance_stream_disconnected", connected=received_any) from exc
        except Exception as exc:  # pragma: no cover - defensive: never crash silently
            raise _StreamFailure("binance_stream_disconnected", connected=received_any) from exc
        finally:
            if client is not None:
                await client.close_connection()


def make_binance_feed_starter(
    *,
    base_url: str,
    client_factory: ClientFactory = AsyncClient.create,
    socket_manager_factory: SocketManagerFactory = BinanceSocketManager,
    silence_timeout_seconds: float = DEFAULT_SILENCE_TIMEOUT_SECONDS,
) -> Callable[[StreamFeedKey, FeedSink], FeedWorkerHandle]:
    """Builds a `FeedStarter` (see candle_stream.py) for Binance kline feeds.
    `base_url` is validated the same way as the REST testnet client (see
    module docstring); it is not otherwise used, since the websocket URL is
    resolved internally from `testnet=True`, never from a caller-supplied
    value. Work unit 5 (ticket verification + WS termination) is expected to
    call this once per resolved workspace connection."""
    BinanceSpotTestnetClient._validate_testnet_url(base_url)

    def starter(key: StreamFeedKey, sink: FeedSink) -> FeedWorkerHandle:
        if key.exchange != "binance":
            raise ValueError(f"make_binance_feed_starter cannot start a {key.exchange!r} feed")
        worker = BinanceFeedWorker(
            symbol=key.symbol,
            timeframe=key.timeframe,
            sink=sink,
            silence_timeout_seconds=silence_timeout_seconds,
            client_factory=client_factory,
            socket_manager_factory=socket_manager_factory,
        )
        return worker.start()

    return starter
