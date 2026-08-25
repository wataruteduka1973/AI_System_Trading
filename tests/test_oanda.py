import asyncio
from unittest.mock import MagicMock

from app.exchanges.oanda import OandaApiError, OandaPracticeClient, mask_account_id
from oandapyV20.endpoints.accounts import AccountInstruments, AccountList, AccountSummary


def test_oanda_practice_client_uses_sdk_and_reads_accounts() -> None:
    sdk_client = MagicMock()

    def request(endpoint: object) -> dict[str, object]:
        if isinstance(endpoint, AccountList):
            return {"accounts": [{"id": "101-001-12345678-001"}]}
        if isinstance(endpoint, AccountSummary):
            return {
                "account": {
                    "id": "101-001-12345678-001",
                    "alias": "Practice",
                    "currency": "JPY",
                    "hedgingEnabled": True,
                    "marginRate": "0.04",
                    "guaranteedStopLossOrderMode": "DISABLED",
                }
            }
        if isinstance(endpoint, AccountInstruments):
            return {"instruments": [{"name": "USD_JPY"}]}
        raise AssertionError(f"Unexpected endpoint: {endpoint!r}")

    sdk_client.request.side_effect = request
    api_factory = MagicMock(return_value=sdk_client)
    accounts = asyncio.run(
        OandaPracticeClient(api_factory=api_factory).verify_and_list_accounts(
            "https://api-fxpractice.oanda.com", "private-token"
        )
    )

    api_factory.assert_called_once_with(
        access_token="private-token",
        environment="practice",
        request_params={"timeout": 10.0},
    )
    sdk_client.client.close.assert_called_once()
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


def test_oanda_client_reads_usd_jpy_instrument_rules() -> None:
    sdk_client = MagicMock()
    sdk_client.request.return_value = {
        "instruments": [
            {
                "name": "USD_JPY",
                "type": "CURRENCY",
                "displayPrecision": 3,
                "tradeUnitsPrecision": 0,
                "pipLocation": -2,
                "minimumTradeSize": "1",
                "maximumOrderUnits": "100000000",
            }
        ]
    }
    client = OandaPracticeClient(api_factory=MagicMock(return_value=sdk_client))

    rules = asyncio.run(
        client.get_instrument_rules(
            "https://api-fxpractice.oanda.com", "private-token", "private-account-id"
        )
    )

    endpoint = sdk_client.request.call_args.args[0]
    assert isinstance(endpoint, AccountInstruments)
    assert rules.symbol == "USD_JPY"
    assert str(rules.tick_size) == "0.001"
    assert str(rules.step_size) == "1"
    assert str(rules.min_quantity) == "1"
    sdk_client.client.close.assert_called_once()
