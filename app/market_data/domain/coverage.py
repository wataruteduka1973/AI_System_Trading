"""Which candles a stored series is missing, and how complete it is for a requested
range. Pure calculations over open times and counts; reading and writing the stored
candles and gaps is `infrastructure` (docs/plans/market-data-services-consolidation.md)."""

from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.exchanges.types import timeframe_delta


@dataclass(frozen=True)
class GapWindow:
    from_time: datetime
    to_time: datetime
    expected_count: int
    missing_count: int
    reason_code: str = "internal_missing_candles"

    def as_dict(self) -> dict[str, object]:
        values = asdict(self)
        values["from_time"] = self.from_time.isoformat()
        values["to_time"] = self.to_time.isoformat()
        return values


def find_internal_gaps(
    open_times: list[datetime], timeframe: str, exchange_code: str
) -> list[GapWindow]:
    delta = timeframe_delta(timeframe)
    ordered = sorted(set(open_times))
    gaps: list[GapWindow] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        missing_times: list[datetime] = []
        candidate = previous + delta
        while candidate < current:
            if is_expected_market_time(exchange_code, candidate):
                missing_times.append(candidate)
            candidate += delta
        if not missing_times:
            continue
        segment_start = missing_times[0]
        segment_count = 1
        for prior_missing, missing in zip(missing_times, missing_times[1:], strict=False):
            if missing != prior_missing + delta:
                gaps.append(
                    GapWindow(
                        from_time=segment_start,
                        to_time=prior_missing + delta,
                        expected_count=segment_count,
                        missing_count=segment_count,
                    )
                )
                segment_start = missing
                segment_count = 1
            else:
                segment_count += 1
        gaps.append(
            GapWindow(
                from_time=segment_start,
                to_time=missing_times[-1] + delta,
                expected_count=segment_count,
                missing_count=segment_count,
            )
        )
    return gaps


def is_expected_market_time(exchange_code: str, candle_open_time: datetime) -> bool:
    if exchange_code != "oanda":
        return True
    new_york_time = candle_open_time.astimezone(ZoneInfo("America/New_York"))
    weekday = new_york_time.weekday()
    if weekday == 5:
        return False
    if weekday == 4 and new_york_time.hour >= 17:
        return False
    return not (weekday == 6 and new_york_time.hour < 17)


def classify_candle_coverage(
    *,
    exchange_code: str | None,
    timeframe: str,
    requested_from: datetime | None,
    requested_to: datetime | None,
    stored_count: int,
    actual_from: datetime | None,
    actual_to: datetime | None,
    internal_missing_count: int | None = None,
) -> dict[str, object]:
    delta = timeframe_delta(timeframe)
    expected_count: int | None = None
    missing_count: int | None = None
    coverage_status = "empty"
    if stored_count and actual_from is not None and actual_to is not None:
        missing_count = internal_missing_count
        if exchange_code == "binance":
            if requested_from is not None and requested_to is not None:
                expected_count = max(0, int((requested_to - requested_from) / delta))
            if missing_count is None:
                actual_expected = max(1, int((actual_to - actual_from) / delta))
                missing_count = max(0, actual_expected - stored_count)
        starts_in_range = requested_from is None or actual_from <= requested_from + delta
        ends_in_range = requested_to is None or actual_to >= requested_to - delta
        if starts_in_range and ends_in_range and (missing_count in {None, 0}):
            coverage_status = "complete"
        elif missing_count not in {None, 0}:
            coverage_status = "partial_gaps"
        else:
            coverage_status = "partial_source_limit"
    return {
        "requested_from": requested_from.isoformat() if requested_from else None,
        "requested_to": requested_to.isoformat() if requested_to else None,
        "actual_from": actual_from.isoformat() if actual_from else None,
        "actual_to": actual_to.isoformat() if actual_to else None,
        "stored_count": stored_count,
        "expected_count": expected_count,
        "missing_count": missing_count,
        "coverage_status": coverage_status,
        "source_limitation": (
            "binance_testnet_periodic_reset"
            if exchange_code == "binance" and coverage_status == "partial_source_limit"
            else None
        ),
    }
