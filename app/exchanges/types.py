from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_delta(timeframe: str) -> timedelta:
    try:
        return timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


@dataclass(frozen=True)
class CandlePoint:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    trade_count: int | None
    is_final: bool


class BinanceKlineFormatError(ValueError):
    """A Binance kline array (testnet or production -- both exchanges/environments
    use the identical wire format) did not match the expected shape."""


def parse_binance_kline(payload: object) -> CandlePoint:
    """Shared by `app/exchanges/binance.py` (Testnet, authenticated) and
    `app/exchanges/binance_public.py` (production, public/unauthenticated) --
    2026-09-26, extracted rather than duplicated when the public client was
    added, since both exchanges' REST APIs return klines in the same array
    shape: `[open_time_ms, open, high, low, close, volume, close_time_ms, ...,
    trade_count, ...]`."""
    if not isinstance(payload, list) or len(payload) < 9:
        raise BinanceKlineFormatError("Binance candle response contains an invalid candle")
    try:
        point = CandlePoint(
            open_time=datetime.fromtimestamp(int(payload[0]) / 1000, tz=UTC),
            close_time=datetime.fromtimestamp((int(payload[6]) + 1) / 1000, tz=UTC),
            open=Decimal(str(payload[1])),
            high=Decimal(str(payload[2])),
            low=Decimal(str(payload[3])),
            close=Decimal(str(payload[4])),
            volume=Decimal(str(payload[5])),
            trade_count=int(payload[8]),
            is_final=(int(payload[6]) + 1) <= int(datetime.now(UTC).timestamp() * 1000),
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise BinanceKlineFormatError("Binance candle response is missing price data") from exc
    if min(point.open, point.high, point.low, point.close) <= 0:
        raise BinanceKlineFormatError("Binance candle response contains a non-positive price")
    return point
