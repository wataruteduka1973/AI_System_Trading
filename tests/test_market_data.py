from datetime import UTC, datetime, timedelta

import pytest
from app.exchanges.binance import BinanceSpotTestnetClient
from app.exchanges.oanda import OandaPracticeClient
from app.exchanges.types import timeframe_delta
from app.services.market_data import (
    MarketDataAccessError,
    classify_candle_coverage,
    market_data_error_code,
)


def test_supported_timeframes_have_expected_duration() -> None:
    assert timeframe_delta("1m") == timedelta(minutes=1)
    assert timeframe_delta("4h") == timedelta(hours=4)
    assert timeframe_delta("1d") == timedelta(days=1)


def test_unsupported_timeframe_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        timeframe_delta("2m")


def test_oanda_parser_keeps_final_midpoint_candle() -> None:
    candle = OandaPracticeClient._parse_candle(
        {
            "time": "2026-08-25T00:00:00Z",
            "complete": True,
            "volume": 12,
            "mid": {"o": "147.100", "h": "147.200", "l": "147.050", "c": "147.180"},
        },
        "1m",
    )

    assert candle.is_final is True
    assert candle.close_time - candle.open_time == timedelta(minutes=1)
    assert str(candle.close) == "147.180"


def test_binance_parser_keeps_trade_count_and_final_state() -> None:
    candle = BinanceSpotTestnetClient._parse_candle(
        [
            1_700_000_000_000,
            "100.0",
            "110.0",
            "90.0",
            "105.0",
            "1.25",
            1_700_000_059_999,
            "0",
            42,
        ]
    )

    assert candle.is_final is True
    assert candle.trade_count == 42
    assert str(candle.volume) == "1.25"


def test_configuration_errors_are_safe_codes() -> None:
    assert market_data_error_code(MarketDataAccessError("secret details")) == "configuration_error"


def test_binance_testnet_short_history_is_reported_as_source_limit() -> None:
    requested_from = datetime(2025, 8, 25, tzinfo=UTC)
    requested_to = datetime(2026, 8, 25, tzinfo=UTC)

    coverage = classify_candle_coverage(
        exchange_code="binance",
        timeframe="1d",
        requested_from=requested_from,
        requested_to=requested_to,
        stored_count=20,
        actual_from=datetime(2026, 8, 5, tzinfo=UTC),
        actual_to=requested_to,
    )

    assert coverage["coverage_status"] == "partial_source_limit"
    assert coverage["expected_count"] == 365
    assert coverage["missing_count"] == 0
    assert coverage["source_limitation"] == "binance_testnet_periodic_reset"


def test_binance_complete_requested_range_is_reported_as_complete() -> None:
    requested_from = datetime(2026, 8, 5, tzinfo=UTC)
    requested_to = datetime(2026, 8, 25, tzinfo=UTC)

    coverage = classify_candle_coverage(
        exchange_code="binance",
        timeframe="1d",
        requested_from=requested_from,
        requested_to=requested_to,
        stored_count=20,
        actual_from=requested_from,
        actual_to=requested_to,
    )

    assert coverage["coverage_status"] == "complete"
    assert coverage["expected_count"] == 20
    assert coverage["missing_count"] == 0
    assert coverage["source_limitation"] is None


def test_binance_gaps_are_distinct_from_source_range_limit() -> None:
    requested_from = datetime(2026, 8, 5, tzinfo=UTC)
    requested_to = datetime(2026, 8, 25, tzinfo=UTC)

    coverage = classify_candle_coverage(
        exchange_code="binance",
        timeframe="1d",
        requested_from=requested_from,
        requested_to=requested_to,
        stored_count=18,
        actual_from=requested_from,
        actual_to=requested_to,
    )

    assert coverage["coverage_status"] == "partial_gaps"
    assert coverage["missing_count"] == 2
    assert coverage["source_limitation"] is None


def test_empty_coverage_is_reported_without_inventing_expected_rows() -> None:
    coverage = classify_candle_coverage(
        exchange_code="oanda",
        timeframe="1h",
        requested_from=datetime(2026, 8, 24, tzinfo=UTC),
        requested_to=datetime(2026, 8, 25, tzinfo=UTC),
        stored_count=0,
        actual_from=None,
        actual_to=None,
    )

    assert coverage["coverage_status"] == "empty"
    assert coverage["expected_count"] is None
    assert coverage["missing_count"] is None
