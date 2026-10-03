"""What one backfill fetched and wrote, accumulated page by page and stored as the
job's validation result. Pure data; the Worker persists it
(docs/plans/market-data-services-consolidation.md)."""

from dataclasses import dataclass, field
from datetime import datetime

from app.exchanges.types import CandlePoint
from app.market_data.domain.coverage import GapWindow


@dataclass
class IngestionReport:
    requested_from: datetime
    requested_to: datetime
    actual_first_candle_time: datetime | None = None
    actual_last_candle_time: datetime | None = None
    source_rows_received: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    empty_source_window_count: int = 0
    empty_source_window_samples: list[dict[str, str]] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        return self.rows_inserted + self.rows_updated

    def record_empty_window(self, start: datetime, end: datetime) -> None:
        self.empty_source_window_count += 1
        if len(self.empty_source_window_samples) < 20:
            self.empty_source_window_samples.append(
                {"from_time": start.isoformat(), "to_time": end.isoformat()}
            )

    def record_candles(self, points: list[CandlePoint]) -> None:
        if not points:
            return
        first = min(point.open_time for point in points)
        last = max(point.open_time for point in points)
        if self.actual_first_candle_time is None or first < self.actual_first_candle_time:
            self.actual_first_candle_time = first
        if self.actual_last_candle_time is None or last > self.actual_last_candle_time:
            self.actual_last_candle_time = last

    def as_validation_result(
        self, coverage: dict[str, object], gaps: list[GapWindow]
    ) -> dict[str, object]:
        coverage_status = coverage.get("coverage_status")
        safe_reason_code = coverage.get("source_limitation")
        if safe_reason_code is None and coverage_status == "partial_gaps":
            safe_reason_code = "internal_missing_candles"
        elif safe_reason_code is None and coverage_status == "empty":
            safe_reason_code = "empty_source_response"
        return {
            "requested_from": self.requested_from.isoformat(),
            "requested_to": self.requested_to.isoformat(),
            "actual_first_candle_time": (
                self.actual_first_candle_time.isoformat() if self.actual_first_candle_time else None
            ),
            "actual_last_candle_time": (
                self.actual_last_candle_time.isoformat() if self.actual_last_candle_time else None
            ),
            "source_rows_received": self.source_rows_received,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "rows_written": self.rows_written,
            "empty_source_window_count": self.empty_source_window_count,
            "empty_source_window_samples": self.empty_source_window_samples,
            "final_candles_only": True,
            "duplicates": "upserted",
            "internal_gap_count": len(gaps),
            "internal_gap_samples": [gap.as_dict() for gap in gaps[:20]],
            "safe_reason_code": safe_reason_code,
            **coverage,
        }
