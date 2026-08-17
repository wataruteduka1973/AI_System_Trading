# pyright: reportMissingTypeStubs=false

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountInstruments, AccountList, AccountSummary
from oandapyV20.exceptions import V20Error
from requests import RequestException

PRACTICE_HOST = "api-fxpractice.oanda.com"


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
