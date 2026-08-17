from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

import httpx

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
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout = httpx.Timeout(timeout_seconds)

    async def verify_and_list_accounts(self, base_url: str, token: str) -> list[OandaAccount]:
        self._validate_practice_url(base_url)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=self.timeout
        ) as client:
            account_payload = await self._get_json(client, "/v3/accounts")
            account_refs = account_payload.get("accounts")
            if not isinstance(account_refs, list):
                raise OandaApiError("OANDA account response has an invalid format")

            accounts: list[OandaAccount] = []
            for account_ref in account_refs:
                account_id = account_ref.get("id") if isinstance(account_ref, dict) else None
                if not isinstance(account_id, str) or not account_id:
                    raise OandaApiError("OANDA account response is missing an account ID")
                accounts.append(await self._load_account(client, account_id))
            return accounts

    async def _load_account(self, client: httpx.AsyncClient, account_id: str) -> OandaAccount:
        summary_payload = await self._get_json(client, f"/v3/accounts/{account_id}/summary")
        summary = summary_payload.get("account")
        if not isinstance(summary, dict):
            raise OandaApiError("OANDA account summary has an invalid format")
        instruments_payload = await self._get_json(
            client,
            f"/v3/accounts/{account_id}/instruments",
            params={"instruments": "USD_JPY"},
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

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            response = await client.get(path, params=params)
        except httpx.RequestError as exc:
            raise OandaApiError("OANDA practice API is unreachable") from exc
        if response.status_code == 401:
            raise OandaAuthenticationError("OANDA rejected the access token")
        if response.status_code >= 400:
            raise OandaApiError(f"OANDA request failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise OandaApiError("OANDA returned invalid JSON") from exc
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
