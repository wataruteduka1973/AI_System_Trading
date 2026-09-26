"""One-off/occasional refresh of real Binance production market data for
backtesting research (2026-09-26, per user decision -- see
app/exchanges/binance_public.py's module docstring for why this exists).

Not a Worker, not started by scripts/start_local.py, not on any schedule:
run manually whenever fresh history is wanted. Paces requests with a short
delay to stay well under Binance's public rate limits and avoid a temporary
IP ban; a full year of 1m data is ~526 requests and will take several minutes.

Run: python scripts/fetch_binance_public_history.py [--symbol BTCJPY] [--days 365]
"""

import argparse
import asyncio

from app.db.session import SessionLocal
from app.exchanges.binance_public import get_binance_public_client
from app.market_data.application.public_research import (
    backfill_public_klines,
    ensure_public_research_instrument,
)
from app.market_data.application.use_cases import SUPPORTED_TIMEFRAMES

_REQUEST_PACING_SECONDS = 0.25


async def _run(symbol: str, days: int) -> None:
    client = get_binance_public_client()
    with SessionLocal() as db:
        instrument = await ensure_public_research_instrument(db, client, symbol)
        db.commit()
        print(f"[OK] {symbol} research instrument ready (id={instrument.id})")
        for timeframe in SUPPORTED_TIMEFRAMES:
            print(f"[..] {symbol} {timeframe}: fetching {days}d of history...")
            inserted, updated = await backfill_public_klines(
                db, client, instrument, timeframe, days
            )
            print(f"[OK] {symbol} {timeframe}: inserted={inserted} updated={updated}")
            await asyncio.sleep(_REQUEST_PACING_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCJPY")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    asyncio.run(_run(args.symbol, args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
