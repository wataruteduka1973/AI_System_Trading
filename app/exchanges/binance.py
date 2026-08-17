# pyright: reportMissingTypeStubs=false

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from binance import AsyncClient
from binance.exceptions import BinanceAPIException, BinanceRequestException

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
