# pyright: reportMissingTypeStubs=false

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from binance import AsyncClient
from binance.exceptions import BinanceAPIException, BinanceRequestException

from app.exchanges.types import CandlePoint

TESTNET_HOST = "testnet.binance.vision"
ClientFactory = Callable[..., Awaitable[AsyncClient]]


class BinanceApiError(RuntimeError):
    pass


class BinanceAuthenticationError(BinanceApiError):
    pass


@dataclass(frozen=True)
class BinanceSpotAccount:
    account_ref: str
    can_trade: bool
    can_deposit: bool
    can_withdraw: bool
    account_type: str
    permissions: tuple[str, ...]
    nonzero_asset_count: int
    btc_jpy_tradeable: bool


@dataclass(frozen=True)
class BinanceInstrumentRules:
    symbol: str
    base_asset: str
    quote_asset: str
    price_scale: int
    quantity_scale: int
    tick_size: Decimal
    step_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal | None
    allowed_order_types: tuple[str, ...]


def mask_api_key(api_key: str) -> str:
    suffix = api_key[-4:] if len(api_key) >= 4 else api_key
    return f"****{suffix}"


class BinanceSpotTestnetClient:
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        client_factory: ClientFactory = AsyncClient.create,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory

    async def verify_account(
        self, base_url: str, api_key: str, secret_key: str
    ) -> BinanceSpotAccount:
        self._validate_testnet_url(base_url)
        client: AsyncClient | None = None
        try:
            client = await self.client_factory(
                api_key=api_key,
                api_secret=secret_key,
                testnet=True,
                requests_params={"timeout": self.timeout_seconds},
            )
            account = self._require_mapping(await client.get_account(recvWindow=5000))
            symbol_info = await client.get_symbol_info("BTCJPY")
            btc_jpy_tradeable = (
                isinstance(symbol_info, dict) and symbol_info.get("status") == "TRADING"
            )
        except BinanceAPIException as exc:
            if exc.status_code in {401, 403} or exc.code in {-2014, -2015}:
                raise BinanceAuthenticationError("Binance rejected the API credentials") from exc
            raise BinanceApiError(f"Binance request failed with HTTP {exc.status_code}") from exc
        except (BinanceRequestException, TimeoutError) as exc:
            raise BinanceApiError("Binance Spot Testnet API is unreachable") from exc
        finally:
            if client is not None:
                await client.close_connection()

        balances = account.get("balances", [])
        if not isinstance(balances, list):
            raise BinanceApiError("Binance account response has an invalid format")
        nonzero_asset_count = sum(
            1
            for balance in balances
            if isinstance(balance, dict)
            and (self._is_nonzero(balance.get("free")) or self._is_nonzero(balance.get("locked")))
        )
        permissions_value = account.get("permissions", [])
        permissions = (
            tuple(str(permission) for permission in permissions_value)
            if isinstance(permissions_value, list)
            else ()
        )
        return BinanceSpotAccount(
            account_ref=api_key,
            can_trade=bool(account.get("canTrade", False)),
            can_deposit=bool(account.get("canDeposit", False)),
            can_withdraw=bool(account.get("canWithdraw", False)),
            account_type=str(account.get("accountType", "SPOT")),
            permissions=permissions,
            nonzero_asset_count=nonzero_asset_count,
            btc_jpy_tradeable=btc_jpy_tradeable,
        )

    async def get_instrument_rules(
        self,
        base_url: str,
        api_key: str,
        secret_key: str,
        symbol: str = "BTCJPY",
    ) -> BinanceInstrumentRules:
        self._validate_testnet_url(base_url)
        client: AsyncClient | None = None
        try:
            client = await self.client_factory(
                api_key=api_key,
                api_secret=secret_key,
                testnet=True,
                requests_params={"timeout": self.timeout_seconds},
            )
            payload = await client.get_symbol_info(symbol)
            return self._parse_instrument_rules(payload, symbol)
        except BinanceAPIException as exc:
            if exc.status_code in {401, 403} or exc.code in {-2014, -2015}:
                raise BinanceAuthenticationError("Binance rejected the API credentials") from exc
            raise BinanceApiError(f"Binance request failed with HTTP {exc.status_code}") from exc
        except (BinanceRequestException, TimeoutError) as exc:
            raise BinanceApiError("Binance Spot Testnet API is unreachable") from exc
        finally:
            if client is not None:
                await client.close_connection()

    async def get_candles(
        self,
        base_url: str,
        api_key: str,
        secret_key: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[CandlePoint]:
        self._validate_testnet_url(base_url)
        client: AsyncClient | None = None
        try:
            client = await self.client_factory(
                api_key=api_key,
                api_secret=secret_key,
                testnet=True,
                requests_params={"timeout": self.timeout_seconds},
            )
            payload = await client.get_klines(
                symbol=symbol,
                interval=timeframe,
                startTime=int(start.astimezone(UTC).timestamp() * 1000),
                endTime=int(end.astimezone(UTC).timestamp() * 1000),
                limit=1000,
            )
            if not isinstance(payload, list):
                raise BinanceApiError("Binance candle response has an invalid format")
            return [self._parse_candle(item) for item in payload]
        except BinanceAPIException as exc:
            if exc.status_code in {401, 403} or exc.code in {-2014, -2015}:
                raise BinanceAuthenticationError("Binance rejected the API credentials") from exc
            raise BinanceApiError(
                f"Binance candle request failed with HTTP {exc.status_code}"
            ) from exc
        except (BinanceRequestException, TimeoutError) as exc:
            raise BinanceApiError("Binance Spot Testnet API is unreachable") from exc
        finally:
            if client is not None:
                await client.close_connection()

    @staticmethod
    def _parse_instrument_rules(
        payload: object, expected_symbol: str
    ) -> BinanceInstrumentRules:
        if not isinstance(payload, dict) or payload.get("symbol") != expected_symbol:
            raise BinanceApiError(f"Binance did not return rules for {expected_symbol}")
        filters = payload.get("filters")
        if not isinstance(filters, list):
            raise BinanceApiError("Binance symbol response is missing filters")
        filters_by_type = {
            item.get("filterType"): item for item in filters if isinstance(item, dict)
        }
        try:
            price_filter = filters_by_type["PRICE_FILTER"]
            lot_filter = filters_by_type["LOT_SIZE"]
            notional_filter = filters_by_type.get("NOTIONAL") or filters_by_type.get(
                "MIN_NOTIONAL"
            )
            tick_size = Decimal(str(price_filter["tickSize"]))
            step_size = Decimal(str(lot_filter["stepSize"]))
            min_quantity = Decimal(str(lot_filter["minQty"]))
            max_quantity = Decimal(str(lot_filter["maxQty"]))
            min_notional = (
                Decimal(str(notional_filter["minNotional"])) if notional_filter else None
            )
            price_scale = BinanceSpotTestnetClient._decimal_scale(tick_size)
            quantity_scale = BinanceSpotTestnetClient._decimal_scale(step_size)
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise BinanceApiError("Binance symbol response is missing trading rules") from exc
        if any(value <= 0 for value in (tick_size, step_size, min_quantity, max_quantity)):
            raise BinanceApiError("Binance symbol response contains invalid trading rules")
        order_types = payload.get("orderTypes")
        return BinanceInstrumentRules(
            symbol=expected_symbol,
            base_asset=str(payload.get("baseAsset", "")),
            quote_asset=str(payload.get("quoteAsset", "")),
            price_scale=price_scale,
            quantity_scale=quantity_scale,
            tick_size=tick_size,
            step_size=step_size,
            min_quantity=min_quantity,
            max_quantity=max_quantity,
            min_notional=min_notional,
            allowed_order_types=(
                tuple(str(item) for item in order_types) if isinstance(order_types, list) else ()
            ),
        )

    @staticmethod
    def _decimal_scale(value: Decimal) -> int:
        return max(0, -value.normalize().as_tuple().exponent)

    @staticmethod
    def _parse_candle(payload: object) -> CandlePoint:
        if not isinstance(payload, list) or len(payload) < 9:
            raise BinanceApiError("Binance candle response contains an invalid candle")
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
            raise BinanceApiError("Binance candle response is missing price data") from exc
        if min(point.open, point.high, point.low, point.close) <= 0:
            raise BinanceApiError("Binance candle response contains a non-positive price")
        return point

    @staticmethod
    def _require_mapping(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BinanceApiError("Binance returned an invalid response")
        return payload

    @staticmethod
    def _is_nonzero(value: object) -> bool:
        try:
            return float(str(value)) != 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _validate_testnet_url(base_url: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or parsed.hostname != TESTNET_HOST or parsed.port is not None:
            raise BinanceApiError("Only the official Binance Spot Testnet API URL is allowed")


def get_binance_spot_testnet_client() -> BinanceSpotTestnetClient:
    return BinanceSpotTestnetClient()
