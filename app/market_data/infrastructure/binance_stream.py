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
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from binance import AsyncClient
from binance.exceptions import BinanceAPIException, BinanceRequestException, ReadLoopClosed
from binance.ws.streams import BinanceSocketManager

from app.exchanges.binance import BinanceSpotTestnetClient, ClientFactory
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    FeedSink,
    FeedWorkerHandle,
    NormalizedCandleUpdate,
    StreamFeedKey,
)

SocketManagerFactory = Callable[[AsyncClient], BinanceSocketManager]


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
    queue overflow). Never surfaced verbatim -- see `_run`."""
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
    """Runs one `BinanceSocketManager.kline_socket` connection as a plain
    `asyncio.Task` on the caller's own event loop and reports normalized
    candle updates (and failures) into a FeedHub feed via `sink`. See
    module docstring for why this needs no thread, unlike `OandaFeedWorker`.
    """

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        sink: FeedSink,
        client_factory: ClientFactory = AsyncClient.create,
        socket_manager_factory: SocketManagerFactory = BinanceSocketManager,
    ) -> None:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        self._symbol = symbol
        self._timeframe = timeframe
        self._sink = sink
        self._client_factory = client_factory
        self._socket_manager_factory = socket_manager_factory

    def start(self) -> FeedWorkerHandle:
        task = asyncio.create_task(
            self._run(), name=f"binance-stream-{self._symbol}-{self._timeframe}"
        )
        return _TaskWorkerHandle(task)

    async def _run(self) -> None:
        client: AsyncClient | None = None
        try:
            try:
                client = await self._client_factory(testnet=True)
            except (BinanceAPIException, BinanceRequestException, TimeoutError, OSError):
                self._sink.report_failure("binance_unreachable")
                return
            socket_manager = self._socket_manager_factory(client)
            socket = socket_manager.kline_socket(self._symbol, interval=self._timeframe)
            async with socket as stream:
                while True:
                    message = await stream.recv()
                    update = parse_kline_message(message)
                    if update is not None:
                        self._sink.publish_candle(update)
                    elif _is_stream_error(message):
                        self._sink.report_failure("binance_stream_disconnected")
                        return
        except asyncio.CancelledError:
            raise
        except ReadLoopClosed:
            self._sink.report_failure("binance_stream_disconnected")
        except Exception:  # pragma: no cover - defensive: never crash silently
            self._sink.report_failure("binance_stream_disconnected")
        finally:
            if client is not None:
                await client.close_connection()


def make_binance_feed_starter(
    *,
    base_url: str,
    client_factory: ClientFactory = AsyncClient.create,
    socket_manager_factory: SocketManagerFactory = BinanceSocketManager,
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
            client_factory=client_factory,
            socket_manager_factory=socket_manager_factory,
        )
        return worker.start()

    return starter
