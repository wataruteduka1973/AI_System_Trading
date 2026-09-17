"""OANDA PricingStream adapter: connects to OANDA's real-time price stream
and normalizes ticks into candle events for a FeedHub.

See docs/design/modules/realtime-market-data-stream.md section 6 for the
tick-to-candle normalization approach, and
docs/plans/realtime-market-data-stream.md (work unit 3) for scope.

`PricingStream` (oandapyV20) is a blocking generator backed by a chunked
`requests` HTTP response -- there is no async API for it. `OandaFeedWorker`
therefore drives it from a dedicated background thread and hands normalized
updates back to the FeedHub's event loop via `loop.call_soon_threadsafe`,
matching what `candle_stream.FeedStarter` expects.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from oandapyV20 import API
from oandapyV20.endpoints.pricing import PricingStream
from oandapyV20.exceptions import StreamTerminated, V20Error
from requests import RequestException

from app.exchanges.oanda import OandaPracticeClient
from app.exchanges.types import TIMEFRAME_SECONDS
from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    FeedSink,
    FeedWorkerHandle,
    NormalizedCandleUpdate,
    StreamFeedKey,
)

_REQUEST_TIMEOUT_SECONDS = 65.0
"""OANDA sends a heartbeat roughly every 5s over the stream; a generous
multiple guards against a genuinely stalled TCP connection without
over-reacting to ordinary jitter."""


class OandaStreamError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OandaPriceTick:
    time: datetime
    mid: Decimal


def parse_price_tick(payload: object) -> OandaPriceTick | None:
    """Parse one OANDA streaming line. Returns None for anything that is not
    a usable price tick (heartbeats, non-tradeable/empty quotes, or a
    malformed line) so the stream can tolerate them without crashing."""
    if not isinstance(payload, dict) or payload.get("type") != "PRICE":
        return None
    try:
        tick_time = datetime.fromisoformat(str(payload["time"]).replace("Z", "+00:00"))
        bids = payload["bids"]
        asks = payload["asks"]
        if not bids or not asks:
            return None
        bid = Decimal(str(bids[0]["price"]))
        ask = Decimal(str(asks[0]["price"]))
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return OandaPriceTick(time=tick_time, mid=mid)


def iter_oanda_price_ticks(
    *,
    base_url: str,
    token: str,
    account_id: str,
    symbol: str,
    stop_event: threading.Event,
    api_factory: Callable[..., API] = API,
) -> Iterator[OandaPriceTick]:
    """Blocking generator over one instrument's OANDA price ticks. Must be
    driven from a dedicated thread (see OandaFeedWorker) -- iterating it
    performs blocking network reads. `stop_event` is checked after each
    received line, so a caller-requested stop takes effect within about one
    OANDA heartbeat interval even when no price ticks are arriving."""
    OandaPracticeClient._validate_practice_url(base_url)
    client = api_factory(
        access_token=token,
        environment="practice",
        request_params={"timeout": _REQUEST_TIMEOUT_SECONDS},
    )
    stream = PricingStream(accountID=account_id, params={"instruments": symbol})
    try:
        response = client.request(stream)
        for payload in response:
            tick = parse_price_tick(payload)
            if tick is not None:
                yield tick
            if stop_event.is_set():
                stream.terminate("feed stopped")
    except StreamTerminated:
        pass
    except V20Error as exc:
        code = "oanda_authentication_failed" if exc.code in (401, 403) else "oanda_stream_failed"
        raise OandaStreamError(code, f"OANDA price stream failed with HTTP {exc.code}") from exc
    except RequestException as exc:
        raise OandaStreamError("oanda_unreachable", "OANDA practice stream is unreachable") from exc
    finally:
        session = getattr(client, "client", None)
        if session is not None:
            session.close()


class _MutableOHLCV:
    __slots__ = ("open", "high", "low", "close", "tick_count")

    def __init__(self, price: Decimal) -> None:
        self.open = self.high = self.low = self.close = price
        self.tick_count = 1

    def update(self, price: Decimal) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.tick_count += 1

    def snapshot(self) -> OHLCV:
        return OHLCV(self.open, self.high, self.low, self.close, Decimal(self.tick_count))


@dataclass
class _Bucket:
    open_time: datetime
    ohlcv: _MutableOHLCV


class TickToCandleNormalizer:
    """Aggregates a chronological stream of mid-price ticks into per-timeframe
    candles. See module docstring and design doc section 6.

    Bucket boundaries are simple UTC-epoch-aligned windows
    (open_time = floor(tick_time, timeframe)). This matches OANDA's own
    intraday granularities (1m/5m/15m/30m/1h/4h) but NOT its broker-day
    daily candle boundary (around 21:00-22:00 UTC, not UTC midnight) -- live
    '1d' synthesis is therefore a known MVP simplification and will not line
    up exactly with OANDA's REST daily candles. Volume is the number of
    ticks observed in the bucket, mirroring how OANDA's REST candle API
    itself reports 'volume' as a tick count rather than traded size (see
    app.exchanges.oanda.OandaPracticeClient._parse_candle).
    """

    def __init__(self, timeframe: str) -> None:
        try:
            self._bucket_seconds = TIMEFRAME_SECONDS[timeframe]
        except KeyError as exc:
            raise ValueError(f"Unsupported timeframe: {timeframe}") from exc
        self._bucket: _Bucket | None = None

    def add_tick(self, tick_time: datetime, mid_price: Decimal) -> list[NormalizedCandleUpdate]:
        if mid_price <= 0:
            return []
        bucket_open = self._floor(tick_time)
        bucket = self._bucket
        if bucket is None:
            self._start_bucket(bucket_open, mid_price)
            return [self._current_update(is_final=False)]
        if bucket_open < bucket.open_time:
            return []  # stale/out-of-order tick for an already-finalized bucket
        if bucket_open == bucket.open_time:
            bucket.ohlcv.update(mid_price)
            return [self._current_update(is_final=False)]
        finalized = self._current_update(is_final=True)
        self._start_bucket(bucket_open, mid_price)
        return [finalized, self._current_update(is_final=False)]

    def _floor(self, tick_time: datetime) -> datetime:
        epoch_seconds = int(tick_time.timestamp())
        floored = (epoch_seconds // self._bucket_seconds) * self._bucket_seconds
        return datetime.fromtimestamp(floored, tz=UTC)

    def _start_bucket(self, open_time: datetime, price: Decimal) -> None:
        self._bucket = _Bucket(open_time, _MutableOHLCV(price))

    def _current_update(self, *, is_final: bool) -> NormalizedCandleUpdate:
        bucket = self._bucket
        assert bucket is not None
        return NormalizedCandleUpdate(bucket.open_time, bucket.ohlcv.snapshot(), is_final)


class _ThreadWorkerHandle:
    def __init__(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event

    def stop(self) -> None:
        self._stop_event.set()


class OandaFeedWorker:
    """Runs one OANDA PricingStream connection on a background thread and
    reports normalized candle updates (and failures) into a FeedHub feed
    via `sink`."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        account_id: str,
        symbol: str,
        timeframe: str,
        sink: FeedSink,
        loop: asyncio.AbstractEventLoop,
        tick_source: Callable[..., Iterator[OandaPriceTick]] = iter_oanda_price_ticks,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._account_id = account_id
        self._symbol = symbol
        self._timeframe = timeframe
        self._sink = sink
        self._loop = loop
        self._tick_source = tick_source
        self._stop_event = threading.Event()
        self._normalizer = TickToCandleNormalizer(timeframe)

    def start(self) -> FeedWorkerHandle:
        thread = threading.Thread(
            target=self._run,
            name=f"oanda-stream-{self._symbol}-{self._timeframe}",
            daemon=True,
        )
        thread.start()
        return _ThreadWorkerHandle(self._stop_event)

    def _run(self) -> None:
        try:
            for tick in self._tick_source(
                base_url=self._base_url,
                token=self._token,
                account_id=self._account_id,
                symbol=self._symbol,
                stop_event=self._stop_event,
            ):
                for update in self._normalizer.add_tick(tick.time, tick.mid):
                    self._loop.call_soon_threadsafe(self._sink.publish_candle, update)
        except OandaStreamError as exc:
            self._loop.call_soon_threadsafe(self._sink.report_failure, exc.code)
        except Exception:  # pragma: no cover - defensive: never crash silently
            self._loop.call_soon_threadsafe(self._sink.report_failure, "oanda_stream_failed")


def make_oanda_feed_starter(
    *,
    base_url: str,
    token: str,
    account_id: str,
    loop: asyncio.AbstractEventLoop,
    tick_source: Callable[..., Iterator[OandaPriceTick]] = iter_oanda_price_ticks,
) -> Callable[[StreamFeedKey, FeedSink], FeedWorkerHandle]:
    """Builds a `FeedStarter` (see candle_stream.py) bound to one already-
    resolved OANDA connection. Work unit 5 (ticket verification + WS
    termination) is expected to call this once it has decrypted the
    credentials for the first subscriber of a feed."""

    def starter(key: StreamFeedKey, sink: FeedSink) -> FeedWorkerHandle:
        if key.exchange != "oanda":
            raise ValueError(f"make_oanda_feed_starter cannot start a {key.exchange!r} feed")
        worker = OandaFeedWorker(
            base_url=base_url,
            token=token,
            account_id=account_id,
            symbol=key.symbol,
            timeframe=key.timeframe,
            sink=sink,
            loop=loop,
            tick_source=tick_source,
        )
        return worker.start()

    return starter
