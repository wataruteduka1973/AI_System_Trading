from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.exchanges.binance import BinanceApiError, BinanceAuthenticationError
from app.exchanges.types import CandlePoint
from app.market_data.infrastructure.leases import FeedKey, LeaseClaim, WorkRef
from app.market_data.infrastructure.models import WorkerBackfill
from app.market_data.infrastructure.page_access import AccessSnapshot
from app.market_data.infrastructure.page_errors import classify_failure
from app.market_data.infrastructure.pages import PreparedPage, final_points, restore_report
from app.services.market_data import MarketDataAccessError
from binance.exceptions import BinanceAPIException, BinanceRequestException
from oandapyV20.exceptions import V20Error


@pytest.mark.parametrize(
    "saved",
    [
        {"rows_inserted": "not-an-integer"},
        {"rows_inserted": -1},
        {"rows_inserted": True},
        {"empty_source_window_samples": {}},
        {"empty_source_window_samples": [None]},
        {"empty_source_window_samples": [{"from_time": 42, "to_time": "end"}]},
    ],
)
def test_malformed_saved_report_is_rejected_with_safe_code(saved):
    job = WorkerBackfill(
        from_time=datetime(2026, 8, 1, tzinfo=UTC),
        to_time=datetime(2026, 8, 2, tzinfo=UTC),
        progress_report=saved,
    )
    with pytest.raises(MarketDataAccessError) as error:
        restore_report(job)
    assert error.value.code == "invalid_checkpoint"


def test_saved_report_roundtrip_is_bounded_and_preserves_counters():
    job = WorkerBackfill(
        from_time=datetime(2026, 8, 1, tzinfo=UTC),
        to_time=datetime(2026, 8, 2, tzinfo=UTC),
        progress_report={
            "rows_inserted": 2,
            "rows_updated": 3,
            "empty_source_window_count": 25,
            "empty_source_window_samples": [
                {"from_time": "start", "to_time": "end"} for _ in range(25)
            ],
        },
    )
    report = restore_report(job)
    assert report.rows_written == 5 and report.empty_source_window_count == 25
    assert len(report.empty_source_window_samples) == 20


@pytest.mark.parametrize(
    "status,retry",
    [(400, False), (401, False), (403, False), (429, True), (500, True), (503, True)],
)
def test_sdk_failures_classified_without_message_parsing(status, retry):
    for cause in (
        V20Error(status, "synthetic-sensitive"),
        BinanceAPIException(None, status, '{"code":-1,"msg":"sensitive"}'),
    ):
        wrapped = BinanceApiError("sensitive")
        wrapped.__cause__ = cause
        result = classify_failure(wrapped)
        assert result.retryable is retry
        assert "sensitive" not in repr(result)


@pytest.mark.parametrize(
    "value,minimum,retry", [("180", 180, True), ("invalid", 0, True), ("1e300", 0, False)]
)
def test_retry_after_is_respected(value, minimum, retry):
    exc = BinanceAPIException(
        SimpleNamespace(headers={"Retry-After": value}), 429, '{"code":-1,"msg":"sensitive"}'
    )
    result = classify_failure(exc)
    assert result.retryable is retry and result.retry_after >= minimum


@pytest.mark.parametrize(
    "error,retry",
    [
        (TimeoutError(), True),
        (BinanceAuthenticationError("secret"), False),
        (BinanceApiError("invalid response"), False),
        (BinanceRequestException("invalid JSON"), False),
        (RuntimeError("secret"), False),
    ],
)
def test_unknown_sdk_or_internal_errors_are_not_blindly_retried(error, retry):
    assert classify_failure(error).retryable is retry


@pytest.fixture
def page():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    work = WorkRef(FeedKey(uuid4(), uuid4(), "1m"), "backfill", uuid4())
    claim = LeaseClaim(work, uuid4(), uuid4(), None)
    access = AccessSnapshot(
        "binance",
        "BTCJPY",
        "https://testnet.binance.vision",
        uuid4(),
        uuid4(),
        "hidden-secret-ref",
        None,
        start,
    )
    return PreparedPage(
        claim,
        access,
        start,
        start + timedelta(minutes=3),
        start + timedelta(minutes=3),
        start + timedelta(days=1),
    )


def point(page):
    return CandlePoint(
        page.start,
        page.start + timedelta(minutes=1),
        Decimal(100),
        Decimal(101),
        Decimal(99),
        Decimal(100),
        None,
        None,
        True,
    )


def test_page_filters_unfinished_and_inclusive_end_and_deduplicates(page):
    candle = point(page)
    boundary = replace(candle, open_time=page.end, close_time=page.end + timedelta(minutes=1))
    assert final_points(page, [candle, candle, replace(candle, is_final=False), boundary]) == [
        candle
    ]
    assert "hidden-secret-ref" not in repr(page)


@pytest.mark.parametrize(
    "changes",
    [
        {"high": Decimal(90)},
        {"low": Decimal(102)},
        {"open": Decimal("NaN")},
        {"volume": Decimal(-1)},
        {"trade_count": -1},
        {"open_time": datetime(2026, 8, 1)},
    ],
)
def test_invalid_candles_rejected(page, changes):
    with pytest.raises(MarketDataAccessError):
        final_points(page, [replace(point(page), **changes)])


def test_conflicting_duplicates_rejected(page):
    with pytest.raises(MarketDataAccessError):
        final_points(page, [point(page), replace(point(page), close=Decimal(101))])
