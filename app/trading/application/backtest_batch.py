"""Several instruments in one backtest request (docs/plans/backtest-batch.md).

The backtest runs synchronously inside the request and is quick for what is normally asked
(BTCUSDT on 4h over 2.7 years: 0.2 s; on 1h: 1 s; on 5-minute bars, 290,000 of them: 13 s), so a
job queue would add a worker, a queue and a progress screen to solve a problem the usual runs
do not have. What can make a request too slow is the total number of bars, so that is what is
capped: `MAX_BATCH_BARS` across all the instruments, counted **before anything runs**. A request
over the budget is refused whole, naming each instrument's count, instead of running some and
then timing out; the caller narrows the range or the instruments.

Each instrument is its own backtest (its own dataset snapshot and run rows, committed one by
one), exactly what the single-instrument endpoint would have produced; an instrument that fails
(no candles in the range, say) is reported in its own item and does not stop the others. Not a
portfolio simulation: the instruments do not share equity.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun
from app.models.instruments import Instrument
from app.trading.application.backtest_provisioning import (
    BacktestProvisioningError,
    count_final_candles,
    run_backtest_for_workspace,
)
from app.trading.application.backtest_walk_forward import WalkForwardError

logger = structlog.get_logger(__name__)

MAX_BATCH_INSTRUMENTS = 12
MAX_BATCH_BARS = 300_000
"""About 15 seconds of replay at the measured rate (~20,000 bars a second with the Risk Gate)."""


class BatchTooLargeError(BacktestProvisioningError):
    def __init__(self, message: str) -> None:
        super().__init__("batch_too_large", message)


@dataclass
class BatchItem:
    instrument: Instrument
    bars: int
    runs: list[BacktestRun] = field(default_factory=list)
    error_code: str | None = None
    error: str | None = None


def run_backtest_batch(
    db: Session,
    workspace_id: UUID,
    instruments: Sequence[Instrument],
    *,
    timeframe: str,
    from_time: datetime,
    to_time: datetime,
    initial_equity: Decimal,
    spread: Decimal = Decimal(0),
    walk_forward: bool = False,
    train_ratio: Decimal = Decimal("0.7"),
    max_bars: int = MAX_BATCH_BARS,
) -> list[BatchItem]:
    if not 1 <= len(instruments) <= MAX_BATCH_INSTRUMENTS:
        raise BacktestProvisioningError(
            "invalid_batch", f"A batch runs 1 to {MAX_BATCH_INSTRUMENTS} instruments"
        )
    if len({instrument.id for instrument in instruments}) != len(instruments):
        raise BacktestProvisioningError("invalid_batch", "An instrument appears more than once")

    items = [
        BatchItem(
            instrument=instrument,
            bars=count_final_candles(db, instrument.id, timeframe, from_time, to_time),
        )
        for instrument in instruments
    ]
    total = sum(item.bars for item in items)
    if total > max_bars:
        breakdown = ", ".join(f"{item.instrument.symbol} {item.bars:,}本" for item in items)
        raise BatchTooLargeError(
            f"対象の足が合計{total:,}本で、上限の{max_bars:,}本を超えています"
            f"({breakdown})。期間を短くするか、銘柄を減らしてください"
        )

    for item in items:
        try:
            item.runs = run_backtest_for_workspace(
                db,
                workspace_id,
                item.instrument,
                timeframe=timeframe,
                from_time=from_time,
                to_time=to_time,
                initial_equity=initial_equity,
                spread=spread,
                walk_forward=walk_forward,
                train_ratio=train_ratio,
            )
        except (BacktestProvisioningError, WalkForwardError) as exc:
            db.rollback()
            item.error_code, item.error = exc.code, str(exc)
        except Exception as exc:
            # One instrument's unexpected failure must not lose the others' results; only the
            # exception type is kept, never its message.
            db.rollback()
            logger.warning(
                "backtest_batch_instrument_failed",
                instrument=str(item.instrument.id),
                error_type=type(exc).__name__,
            )
            item.error_code, item.error = "internal_error", type(exc).__name__
    return items
