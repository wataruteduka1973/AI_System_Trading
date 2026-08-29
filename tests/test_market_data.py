from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.exchanges.binance import BinanceSpotTestnetClient
from app.exchanges.oanda import OandaPracticeClient
from app.exchanges.types import CandlePoint, timeframe_delta
from app.models.catalog import MarketDataGap
from app.services.market_data import (
    CandleIngestionService,
    IngestionReport,
    MarketDataAccessError,
    classify_candle_coverage,
    find_internal_gaps,
    market_data_error_code,
    persist_internal_gaps,
)


def candle_point(open_time: datetime) -> CandlePoint:
    return CandlePoint(
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        trade_count=3,
        is_final=True,
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


def test_internal_gap_detection_returns_bounded_missing_window() -> None:
    start = datetime(2026, 8, 27, tzinfo=UTC)

    gaps = find_internal_gaps(
        [start, start + timedelta(minutes=1), start + timedelta(minutes=3)],
        "1m",
        "binance",
    )

    assert len(gaps) == 1
    assert gaps[0].from_time == start + timedelta(minutes=2)
    assert gaps[0].to_time == start + timedelta(minutes=3)
    assert gaps[0].missing_count == 1


def test_oanda_weekend_closure_is_not_reported_as_internal_gap() -> None:
    friday_before_close = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
    sunday_reopen = datetime(2026, 8, 30, 21, 0, tzinfo=UTC)

    gaps = find_internal_gaps([friday_before_close, sunday_reopen], "1h", "oanda")

    assert gaps == []


def test_oanda_internal_gap_is_reflected_in_coverage_status() -> None:
    requested_from = datetime(2026, 8, 27, tzinfo=UTC)
    requested_to = requested_from + timedelta(hours=4)

    coverage = classify_candle_coverage(
        exchange_code="oanda",
        timeframe="1h",
        requested_from=requested_from,
        requested_to=requested_to,
        stored_count=3,
        actual_from=requested_from,
        actual_to=requested_to,
        internal_missing_count=1,
    )

    assert coverage["coverage_status"] == "partial_gaps"
    assert coverage["missing_count"] == 1


def test_internal_gaps_are_persisted_and_resolved_after_repair() -> None:
    instrument_id = uuid4()
    start = datetime(2026, 8, 27, tzinfo=UTC)
    session = MagicMock()
    session.scalar.return_value = "binance"
    candle_result = MagicMock()
    candle_result.all.return_value = [start, start + timedelta(minutes=2)]
    no_existing_result = MagicMock()
    no_existing_result.all.return_value = []
    session.scalars.side_effect = [candle_result, no_existing_result]

    gaps = persist_internal_gaps(
        session,
        instrument_id=instrument_id,
        timeframe="1m",
        requested_from=start,
        requested_to=start + timedelta(minutes=3),
    )

    persisted = session.add.call_args.args[0]
    assert len(gaps) == 1
    assert isinstance(persisted, MarketDataGap)
    assert persisted.from_time == start + timedelta(minutes=1)
    assert persisted.reason_code == "internal_missing_candles"

    existing = persisted
    repaired_candle_result = MagicMock()
    repaired_candle_result.all.return_value = [
        start,
        start + timedelta(minutes=1),
        start + timedelta(minutes=2),
    ]
    existing_result = MagicMock()
    existing_result.all.return_value = [existing]
    session.scalars.side_effect = [repaired_candle_result, existing_result]

    repaired_gaps = persist_internal_gaps(
        session,
        instrument_id=instrument_id,
        timeframe="1m",
        requested_from=start,
        requested_to=start + timedelta(minutes=3),
    )

    assert repaired_gaps == []
    assert existing.status == "resolved"
    assert existing.resolved_at is not None


def test_empty_candle_result_does_not_resolve_existing_gap() -> None:
    instrument_id = uuid4()
    start = datetime(2026, 8, 27, tzinfo=UTC)
    existing = MarketDataGap(
        instrument_id=instrument_id,
        timeframe="1m",
        from_time=start,
        to_time=start + timedelta(minutes=1),
        expected_count=1,
        missing_count=1,
        reason_code="internal_missing_candles",
        status="open",
    )
    session = MagicMock()
    session.scalar.return_value = "binance"
    empty_candle_result = MagicMock()
    empty_candle_result.all.return_value = []
    existing_result = MagicMock()
    existing_result.all.return_value = [existing]
    session.scalars.side_effect = [empty_candle_result, existing_result]

    gaps = persist_internal_gaps(
        session,
        instrument_id=instrument_id,
        timeframe="1m",
        requested_from=start,
        requested_to=start + timedelta(minutes=2),
    )

    assert gaps == []
    assert existing.status == "open"
    assert existing.resolved_at is None


def test_ingestion_report_combines_accounting_coverage_and_bounded_samples() -> None:
    start = datetime(2026, 8, 27, tzinfo=UTC)
    report = IngestionReport(requested_from=start, requested_to=start + timedelta(hours=1))
    report.source_rows_received = 3
    report.rows_inserted = 2
    report.rows_updated = 1
    report.record_candles([candle_point(start), candle_point(start + timedelta(minutes=2))])
    for minute in range(25):
        report.record_empty_window(
            start + timedelta(minutes=minute), start + timedelta(minutes=minute + 1)
        )
    gap = find_internal_gaps([start, start + timedelta(minutes=2)], "1m", "binance")[0]

    result = report.as_validation_result(
        {
            "coverage_status": "partial_gaps",
            "expected_count": 3,
            "stored_count": 2,
            "missing_count": 1,
            "source_limitation": None,
        },
        [gap],
    )

    assert result["actual_first_candle_time"] == start.isoformat()
    assert result["actual_last_candle_time"] == (start + timedelta(minutes=2)).isoformat()
    assert result["rows_written"] == 3
    assert result["empty_source_window_count"] == 25
    assert len(result["empty_source_window_samples"]) == 20
    assert result["safe_reason_code"] == "internal_missing_candles"
    assert result["internal_gap_count"] == 1


def test_upsert_reports_actual_insert_and_update_counts() -> None:
    session = MagicMock()
    scalar_result = MagicMock()
    scalar_result.all.return_value = [True, False, True]
    session.scalars.return_value = scalar_result
    service = CandleIngestionService(session, MagicMock())
    start = datetime(2026, 8, 27, tzinfo=UTC)

    inserted, updated = service._upsert_points(
        uuid4(),
        "1m",
        "binance",
        "backfilled",
        [
            candle_point(start),
            candle_point(start + timedelta(minutes=1)),
            candle_point(start + timedelta(minutes=2)),
        ],
    )

    assert inserted == 2
    assert updated == 1
