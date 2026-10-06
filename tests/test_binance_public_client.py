"""`BinancePublicClient.get_book_tickers`: parsing and failure handling of the public
bookTicker response (no credentials, no network -- httpx is mocked with respx)."""

from decimal import Decimal

import httpx
import pytest
import respx
from app.exchanges.binance_public import (
    PRODUCTION_BASE_URL,
    BinancePublicApiError,
    BinancePublicClient,
)

URL = f"{PRODUCTION_BASE_URL}/api/v3/ticker/bookTicker"


@pytest.mark.anyio
@respx.mock
async def test_returns_bid_and_ask_per_symbol_in_one_request() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "BTCUSDT",
                    "bidPrice": "83000.10",
                    "bidQty": "1",
                    "askPrice": "83000.11",
                    "askQty": "1",
                },
                {
                    "symbol": "ETHUSDT",
                    "bidPrice": "2000.00",
                    "bidQty": "1",
                    "askPrice": "2000.01",
                    "askQty": "1",
                },
            ],
        )
    )

    quotes = await BinancePublicClient().get_book_tickers(["BTCUSDT", "ETHUSDT"])

    assert quotes == {
        "BTCUSDT": (Decimal("83000.10"), Decimal("83000.11")),
        "ETHUSDT": (Decimal("2000.00"), Decimal("2000.01")),
    }
    assert route.call_count == 1
    assert route.calls[0].request.url.params["symbols"] == '["BTCUSDT","ETHUSDT"]'
    assert "authorization" not in route.calls[0].request.headers
    assert "x-mbx-apikey" not in route.calls[0].request.headers


@pytest.mark.anyio
async def test_no_symbols_makes_no_request() -> None:
    assert await BinancePublicClient().get_book_tickers([]) == {}


@pytest.mark.anyio
@respx.mock
async def test_a_crossed_or_empty_quote_is_dropped() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "AAAUSDT", "bidPrice": "0.00000000", "askPrice": "0.00000000"},
                {"symbol": "BBBUSDT", "bidPrice": "10", "askPrice": "9"},
                {"symbol": "CCCUSDT", "bidPrice": "10", "askPrice": "10.5"},
            ],
        )
    )

    quotes = await BinancePublicClient().get_book_tickers(["AAAUSDT", "BBBUSDT", "CCCUSDT"])

    assert quotes == {"CCCUSDT": (Decimal("10"), Decimal("10.5"))}


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "payload",
    [{"code": -1121}, [{"symbol": "X"}], [{"symbol": "X", "bidPrice": "abc", "askPrice": "1"}]],
)
async def test_an_unexpected_response_is_an_api_error(payload: object) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(BinancePublicApiError):
        await BinancePublicClient().get_book_tickers(["X"])


@pytest.mark.anyio
@respx.mock
async def test_an_unreachable_api_is_an_api_error() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(BinancePublicApiError):
        await BinancePublicClient().get_book_tickers(["BTCUSDT"])
