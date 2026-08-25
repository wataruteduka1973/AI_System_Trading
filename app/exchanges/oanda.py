# pyright: reportMissingTypeStubs=false

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountInstruments, AccountList, AccountSummary
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.exceptions import V20Error
from requests import RequestException

from app.exchanges.types import CandlePoint, timeframe_delta

PRACTICE_HOST = "api-fxpractice.oanda.com"
OANDA_GRANULARITY = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
}


class OandaApiError(RuntimeError):
    pass


class OandaAuthenticationError(OandaApiError):
    pass


@dataclass(frozen=True)
class OandaAccount:
    account_id: str
    alias: str | None
    currency: str
    hedging_enabled: bool | None
    margin_rate: Decimal | None
    gslo_mode: str | None
    usd_jpy_tradeable: bool


@dataclass(frozen=True)
class OandaInstrumentRules:
    symbol: str
    base_asset: str
    quote_asset: str
    price_scale: int
    quantity_scale: int
    tick_size: Decimal
    step_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal | None
    instrument_type: str


def mask_account_id(account_id: str) -> str:
    compact = account_id.replace("-", "")
    suffix = compact[-4:] if len(compact) >= 4 else compact
    return f"****{suffix}"


class OandaPracticeClient:
    def __init__(
        self, timeout_seconds: float = 10.0, api_factory: Callable[..., API] = API
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.api_factory = api_factory

    async def verify_and_list_accounts(self, base_url: str, token: str) -> list[OandaAccount]:
        self._validate_practice_url(base_url)
        return await asyncio.to_thread(self._verify_and_list_accounts_sync, token)

    async def get_instrument_rules(
        self, base_url: str, token: str, account_id: str, symbol: str = "USD_JPY"
    ) -> OandaInstrumentRules:
        self._validate_practice_url(base_url)
        return await asyncio.to_thread(
            self._get_instrument_rules_sync, token, account_id, symbol
        )

    async def get_candles(
        self,
        base_url: str,
        token: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[CandlePoint]:
        self._validate_practice_url(base_url)
        try:
            granularity = OANDA_GRANULARITY[timeframe]
        except KeyError as exc:
            raise OandaApiError(f"Unsupported OANDA timeframe: {timeframe}") from exc
        return await asyncio.to_thread(
            self._get_candles_sync, token, symbol, timeframe, granularity, start, end
        )

    def _get_candles_sync(
        self,
        token: str,
        symbol: str,
        timeframe: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[CandlePoint]:
        client = self.api_factory(
            access_token=token,
            environment="practice",
            request_params={"timeout": self.timeout_seconds},
        )
        try:
            payload = self._request(
                client,
                InstrumentsCandles(
                    instrument=symbol,
                    params={
                        "from": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                        "to": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                        "granularity": granularity,
                        "price": "M",
                        "smooth": False,
                    },
                ),
            )
            candles = payload.get("candles")
            if not isinstance(candles, list):
                raise OandaApiError("OANDA candle response has an invalid format")
            return [self._parse_candle(item, timeframe) for item in candles]
        except V20Error as exc:
            if exc.code in {401, 403}:
                raise OandaAuthenticationError("OANDA rejected the access token") from exc
            raise OandaApiError(f"OANDA candle request failed with HTTP {exc.code}") from exc
        except RequestException as exc:
            raise OandaApiError("OANDA practice API is unreachable") from exc
        finally:
            session = getattr(client, "client", None)
            if session is not None:
                session.close()

    def _get_instrument_rules_sync(
        self, token: str, account_id: str, symbol: str
    ) -> OandaInstrumentRules:
        client = self.api_factory(
            access_token=token,
            environment="practice",
            request_params={"timeout": self.timeout_seconds},
        )
        try:
            payload = self._request(
                client, AccountInstruments(accountID=account_id, params={"instruments": symbol})
            )
            instruments = payload.get("instruments")
            if not isinstance(instruments, list) or len(instruments) != 1:
                raise OandaApiError(f"OANDA did not return rules for {symbol}")
            return self._parse_instrument_rules(instruments[0], symbol)
        except V20Error as exc:
            if exc.code in {401, 403}:
                raise OandaAuthenticationError("OANDA rejected the access token") from exc
            raise OandaApiError(f"OANDA request failed with HTTP {exc.code}") from exc
        except RequestException as exc:
            raise OandaApiError("OANDA practice API is unreachable") from exc
        finally:
            session = getattr(client, "client", None)
            if session is not None:
                session.close()

    def _verify_and_list_accounts_sync(self, token: str) -> list[OandaAccount]:
        client = self.api_factory(
            access_token=token,
            environment="practice",
            request_params={"timeout": self.timeout_seconds},
        )
        try:
            account_payload = self._request(client, AccountList())
            account_refs = account_payload.get("accounts")
            if not isinstance(account_refs, list):
                raise OandaApiError("OANDA account response has an invalid format")
            accounts: list[OandaAccount] = []
            for account_ref in account_refs:
                account_id = account_ref.get("id") if isinstance(account_ref, dict) else None
                if not isinstance(account_id, str) or not account_id:
                    raise OandaApiError("OANDA account response is missing an account ID")
                accounts.append(self._load_account(client, account_id))
            return accounts
        except V20Error as exc:
            if exc.code in {401, 403}:
                raise OandaAuthenticationError("OANDA rejected the access token") from exc
            raise OandaApiError(f"OANDA request failed with HTTP {exc.code}") from exc
        except RequestException as exc:
            raise OandaApiError("OANDA practice API is unreachable") from exc
        finally:
            session = getattr(client, "client", None)
            if session is not None:
                session.close()

    def _load_account(self, client: API, account_id: str) -> OandaAccount:
        summary_payload = self._request(client, AccountSummary(accountID=account_id))
        summary = summary_payload.get("account")
        if not isinstance(summary, dict):
            raise OandaApiError("OANDA account summary has an invalid format")
        instruments_payload = self._request(
            client,
            AccountInstruments(accountID=account_id, params={"instruments": "USD_JPY"}),
        )
        instruments = instruments_payload.get("instruments", [])
        usd_jpy_tradeable = any(
            isinstance(instrument, dict) and instrument.get("name") == "USD_JPY"
            for instrument in instruments
        )
        margin_rate_value = summary.get("marginRate")
        return OandaAccount(
            account_id=account_id,
            alias=summary.get("alias") if isinstance(summary.get("alias"), str) else None,
            currency=str(summary.get("currency", "")),
            hedging_enabled=(
                summary.get("hedgingEnabled")
                if isinstance(summary.get("hedgingEnabled"), bool)
                else None
            ),
            margin_rate=(
                Decimal(str(margin_rate_value)) if margin_rate_value is not None else None
            ),
            gslo_mode=(
                str(summary["guaranteedStopLossOrderMode"]).lower()
                if summary.get("guaranteedStopLossOrderMode") is not None
                else None
            ),
            usd_jpy_tradeable=usd_jpy_tradeable,
        )

    @staticmethod
    def _parse_instrument_rules(payload: object, expected_symbol: str) -> OandaInstrumentRules:
        if not isinstance(payload, dict) or payload.get("name") != expected_symbol:
            raise OandaApiError("OANDA instrument response has an invalid format")
        try:
            price_scale = int(payload["displayPrecision"])
            quantity_scale = int(payload["tradeUnitsPrecision"])
            min_quantity = Decimal(str(payload["minimumTradeSize"]))
            max_value = payload.get("maximumOrderUnits")
            max_quantity = Decimal(str(max_value)) if max_value is not None else None
            tick_size = Decimal(10) ** -price_scale
            step_size = Decimal(10) ** -quantity_scale
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise OandaApiError("OANDA instrument response is missing trading rules") from exc
        if min_quantity <= 0 or tick_size <= 0 or step_size <= 0:
            raise OandaApiError("OANDA instrument response contains invalid trading rules")
        base_asset, quote_asset = expected_symbol.split("_", maxsplit=1)
        return OandaInstrumentRules(
            symbol=expected_symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_scale=price_scale,
            quantity_scale=quantity_scale,
            tick_size=tick_size,
            step_size=step_size,
            min_quantity=min_quantity,
            max_quantity=max_quantity,
            instrument_type=str(payload.get("type", "")),
        )

    @staticmethod
    def _parse_candle(payload: object, timeframe: str) -> CandlePoint:
        if not isinstance(payload, dict) or not isinstance(payload.get("mid"), dict):
            raise OandaApiError("OANDA candle response contains an invalid candle")
        mid = payload["mid"]
        try:
            open_time = datetime.fromisoformat(str(payload["time"]).replace("Z", "+00:00"))
            point = CandlePoint(
                open_time=open_time,
                close_time=open_time + timeframe_delta(timeframe),
                open=Decimal(str(mid["o"])),
                high=Decimal(str(mid["h"])),
                low=Decimal(str(mid["l"])),
                close=Decimal(str(mid["c"])),
                volume=Decimal(str(payload["volume"])),
                trade_count=None,
                is_final=bool(payload.get("complete", False)),
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise OandaApiError("OANDA candle response is missing price data") from exc
        if min(point.open, point.high, point.low, point.close) <= 0:
            raise OandaApiError("OANDA candle response contains a non-positive price")
        return point

    @staticmethod
    def _request(client: API, endpoint: object) -> dict[str, Any]:
        payload = client.request(endpoint)
        if not isinstance(payload, dict):
            raise OandaApiError("OANDA returned an invalid response")
        return payload

    @staticmethod
    def _validate_practice_url(base_url: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or parsed.hostname != PRACTICE_HOST or parsed.port is not None:
            raise OandaApiError("Only the official OANDA practice API URL is allowed")


def get_oanda_practice_client() -> OandaPracticeClient:
    return OandaPracticeClient()
