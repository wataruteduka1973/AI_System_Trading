"""Gap validation for the stored Binance public history (Phase C of
docs/plans/candle-chart-and-coverage.md).

For each symbol and time frame: finds internal gaps, asks Binance's public API again
for each gap, and re-checks. A gap that is still missing afterwards is a window the
source itself does not have (Binance maintenance outages); it stays as an `ignored`
market_data_gap row so later runs do not ask again. The result is recorded in
audit_log (public_candles.gap_validation).

Run: python scripts/validate_public_history.py --symbols BTCUSDT,ETHUSDT
     [--timeframes 1h,4h,1d]  (default: every supported timeframe)
     [--workspace-id <uuid>]  (default: the only workspace)
"""

import argparse
import asyncio
from uuid import UUID

from app.db.session import SessionLocal
from app.exchanges.binance_public import get_binance_public_client
from app.market_data.application.public_research import (
    find_public_research_instrument,
    resolve_audit_workspace_id,
    validate_public_series,
)
from app.market_data.application.use_cases import SUPPORTED_TIMEFRAMES

_REQUEST_PACING_SECONDS = 0.25


async def _run(symbols: list[str], timeframes: list[str], workspace_id: UUID | None) -> int:
    client = get_binance_public_client()
    unfillable_total = 0
    with SessionLocal() as db:
        audit_workspace_id = resolve_audit_workspace_id(db, workspace_id)
        for symbol in symbols:
            instrument = find_public_research_instrument(db, symbol)
            if instrument is None:
                print(f"[SKIP] {symbol}: no research instrument (run fetch_binance_public_history)")
                continue
            for timeframe in timeframes:
                result = await validate_public_series(
                    db, client, instrument, timeframe, audit_workspace_id=audit_workspace_id
                )
                unfillable_total += len(result.unfillable_windows)
                print(
                    f"[OK] {symbol} {timeframe}: stored={result.stored_count} "
                    f"missing {result.missing_before} -> {result.missing_after} "
                    f"(source-side windows: {len(result.unfillable_windows)})"
                )
                await asyncio.sleep(_REQUEST_PACING_SECONDS)
    return unfillable_total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--timeframes", default=",".join(SUPPORTED_TIMEFRAMES))
    parser.add_argument("--workspace-id", type=UUID, default=None)
    args = parser.parse_args()
    timeframes = args.timeframes.split(",")
    unknown = set(timeframes) - set(SUPPORTED_TIMEFRAMES)
    if unknown:
        parser.error(f"unsupported timeframes: {', '.join(sorted(unknown))}")
    asyncio.run(_run(args.symbols.split(","), timeframes, args.workspace_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
