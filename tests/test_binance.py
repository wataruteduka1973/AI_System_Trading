import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.exchanges.binance import BinanceApiError, BinanceSpotTestnetClient, mask_api_key


@pytest.mark.parametrize("value,expected", [("0.00100", 3), ("100", 0), ("1", 0)])
def test_decimal_scale_for_finite_exchange_rules(value, expected):
    assert BinanceSpotTestnetClient._decimal_scale(Decimal(value)) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_decimal_scale_rejects_nonfinite_exchange_rules(value):
    with pytest.raises(ValueError, match="finite"):
        BinanceSpotTestnetClient._decimal_scale(Decimal(value))


def test_binance_spot_testnet_client_uses_sdk_and_reads_account() -> None:
    sdk_client = MagicMock()
    sdk_client.get_account = AsyncMock(
        return_value={
            "canTrade": True,
            "canDeposit": False,
            "canWithdraw": False,
            "accountType": "SPOT",
            "permissions": ["SPOT"],
            "balances": [
                {"asset": "BTC", "free": "0.01000000", "locked": "0.00000000"},
                {"asset": "JPY", "free": "0.00000000", "locked": "0.00000000"},
            ],
        }
    )
    sdk_client.get_symbol_info = AsyncMock(return_value={"symbol": "BTCJPY", "status": "TRADING"})
    sdk_client.close_connection = AsyncMock()
    client_factory = AsyncMock(return_value=sdk_client)

    account = asyncio.run(
        BinanceSpotTestnetClient(client_factory=client_factory).verify_account(
            "https://testnet.binance.vision", "public-api-key", "private-secret"
        )
    )

    client_factory.assert_awaited_once_with(
        api_key="public-api-key",
        api_secret="private-secret",
        testnet=True,
        requests_params={"timeout": 10.0},
    )
    sdk_client.get_account.assert_awaited_once_with(recvWindow=5000)
    sdk_client.get_symbol_info.assert_awaited_once_with("BTCJPY")
    sdk_client.close_connection.assert_awaited_once()
    assert account.nonzero_asset_count == 1
    assert account.btc_jpy_tradeable is True
    assert mask_api_key(account.account_ref) == "****-key"


def test_binance_client_rejects_non_testnet_host() -> None:
    try:
        asyncio.run(
            BinanceSpotTestnetClient().verify_account(
                "https://example.com", "public-api-key", "private-secret"
            )
        )
    except BinanceApiError as exc:
        assert "Testnet" in str(exc)
    else:
        raise AssertionError("Non-testnet host was accepted")


def test_binance_client_reads_btc_jpy_instrument_rules() -> None:
    sdk_client = MagicMock()
    sdk_client.get_symbol_info = AsyncMock(
        return_value={
            "symbol": "BTCJPY",
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "JPY",
            "baseAssetPrecision": 8,
            "quotePrecision": 8,
            "orderTypes": ["LIMIT", "MARKET"],
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "1.00000000"},
                {
                    "filterType": "LOT_SIZE",
                    "stepSize": "0.00001000",
                    "minQty": "0.00001000",
                    "maxQty": "100.00000000",
                },
                {"filterType": "MIN_NOTIONAL", "minNotional": "1000.00000000"},
            ],
        }
    )
    sdk_client.close_connection = AsyncMock()
    client = BinanceSpotTestnetClient(client_factory=AsyncMock(return_value=sdk_client))

    rules = asyncio.run(
        client.get_instrument_rules(
            "https://testnet.binance.vision", "public-api-key", "private-secret"
        )
    )

    sdk_client.get_symbol_info.assert_awaited_once_with("BTCJPY")
    sdk_client.close_connection.assert_awaited_once()
    assert rules.symbol == "BTCJPY"
    assert str(rules.step_size) == "0.00001000"
    assert str(rules.min_notional) == "1000.00000000"
    assert rules.price_scale == 0
    assert rules.quantity_scale == 5
    assert rules.allowed_order_types == ("LIMIT", "MARKET")
