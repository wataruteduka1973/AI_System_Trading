import asyncio

import httpx
import respx
from app.exchanges.oanda import OandaApiError, OandaPracticeClient, mask_account_id


@respx.mock
def test_oanda_practice_client_reads_accounts_and_usd_jpy_capability() -> None:
    accounts_route = respx.get("https://api-fxpractice.oanda.com/v3/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": [{"id": "101-001-12345678-001"}]})
    )
    respx.get("https://api-fxpractice.oanda.com/v3/accounts/101-001-12345678-001/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "account": {
                    "id": "101-001-12345678-001",
                    "alias": "Practice",
                    "currency": "JPY",
                    "hedgingEnabled": True,
                    "marginRate": "0.04",
                    "guaranteedStopLossOrderMode": "DISABLED",
                }
            },
        )
    )
    respx.get(
        "https://api-fxpractice.oanda.com/v3/accounts/101-001-12345678-001/instruments",
        params={"instruments": "USD_JPY"},
    ).mock(return_value=httpx.Response(200, json={"instruments": [{"name": "USD_JPY"}]}))

    accounts = asyncio.run(
        OandaPracticeClient().verify_and_list_accounts(
            "https://api-fxpractice.oanda.com", "private-token"
        )
    )

    assert accounts_route.calls[0].request.headers["Authorization"] == "Bearer private-token"
    assert accounts[0].currency == "JPY"
    assert accounts[0].usd_jpy_tradeable is True
    assert mask_account_id(accounts[0].account_id) == "****8001"


def test_oanda_client_rejects_non_practice_host() -> None:
    try:
        asyncio.run(
            OandaPracticeClient().verify_and_list_accounts("https://example.com", "private-token")
        )
    except OandaApiError as exc:
        assert "practice" in str(exc)
    else:
        raise AssertionError("Non-practice host was accepted")
