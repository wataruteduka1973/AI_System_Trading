"""Public, unauthenticated Binance **production** market data client -- distinct
from `BinanceSpotTestnetClient` (`app/exchanges/binance.py`), which remains the
only client ever used for actual paper-trading account access/execution.

This client exists solely to fetch real historical klines for backtesting
research (2026-09-26, per user decision): a data-quality check found that
Binance Spot Testnet's own BTCJPY candle history is ~95-99% flat/zero-volume at
short timeframes (1m/15m) due to thin testnet liquidity, making it unsuitable
for validating whether a trading strategy would actually work. Production
BTCJPY, confirmed live and liquid via `GET /api/v3/exchangeInfo`, does not have
this problem. This client never places orders, never needs credentials (klines
is a public endpoint), and is never wired into live/paper execution -- see
`docs/architecture-alignment-and-long-term-roadmap.md`'s 承認ゲート
"Binance Public履歴の併用" for the approved scope, and
`app/market_data/application/public_research.py` for the one caller.
"""

from datetime import UTC, datetime

import httpx

from app.exchanges.binance import BinanceApiError, BinanceInstrumentRules, BinanceSpotTestnetClient
from app.exchanges.types import BinanceKlineFormatError, CandlePoint, parse_binance_kline

PRODUCTION_BASE_URL = "https://api.binance.com"
MAX_KLINES_PER_REQUEST = 1000


class BinancePublicApiError(RuntimeError):
    pass


class BinancePublicClient:
    """No API key/secret anywhere in this class -- `/api/v3/klines` and
    `/api/v3/exchangeInfo` are public endpoints on Binance's own production
    API and require none."""

    def __init__(self, timeout_seconds: float = 10.0, base_url: str = PRODUCTION_BASE_URL) -> None:
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url

    async def get_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[CandlePoint]:
        """One page, up to `MAX_KLINES_PER_REQUEST` candles -- mirrors
        `BinanceSpotTestnetClient.get_candles`'s own single-page contract; the
        caller (`public_research.py`) is responsible for paging across a
        larger [start, end) range, exactly as `CandleIngestionService.sync`
        already does for the Testnet client."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}/api/v3/klines",
                    params={
                        "symbol": symbol,
                        "interval": timeframe,
                        "startTime": int(start.astimezone(UTC).timestamp() * 1000),
                        "endTime": int(end.astimezone(UTC).timestamp() * 1000),
                        "limit": MAX_KLINES_PER_REQUEST,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BinancePublicApiError("Could not reach Binance's public API") from exc
        payload = response.json()
        if not isinstance(payload, list):
            raise BinancePublicApiError("Binance candle response has an invalid format")
        try:
            return [parse_binance_kline(item) for item in payload]
        except BinanceKlineFormatError as exc:
            raise BinancePublicApiError(str(exc)) from exc

    async def get_instrument_rules(self, symbol: str) -> BinanceInstrumentRules:
        """Real production trading filters (tick/step size, notional floor) for
        `symbol` -- reuses `BinanceSpotTestnetClient`'s own filter-parsing logic
        (`BinanceInstrumentRules` is exchange-format-agnostic; Testnet and
        production both expose the same `/exchangeInfo` symbol filter shape)
        rather than duplicating it, even though this call itself hits the
        public production endpoint, not Testnet."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}/api/v3/exchangeInfo", params={"symbol": symbol}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BinancePublicApiError("Could not reach Binance's public API") from exc
        payload = response.json()
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        symbol_info = symbols[0] if isinstance(symbols, list) and symbols else None
        try:
            return BinanceSpotTestnetClient._parse_instrument_rules(symbol_info, symbol)
        except BinanceApiError as exc:
            raise BinancePublicApiError(str(exc)) from exc


def get_binance_public_client() -> BinancePublicClient:
    return BinancePublicClient()
