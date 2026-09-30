"""Strategy-independent comparison of the research instrument's timeframes
(2026-09-30): which bar interval gives a strategy the best chance of beating
costs, before any particular strategy is involved.

Per timeframe, over the whole stored history:

- `med|r|`: median absolute close-to-close return per bar. `cost_x` is that
  divided by one Binance round trip (2 x 0.1% fee, slippage 0 as in the
  backtest) -- below ~1, a typical single bar's move does not even pay for
  one round trip, so a strategy on that timeframe must hold for many bars.
- `ac1`: lag-1 autocorrelation of per-bar returns. Positive = moves tend to
  continue (favours trend-following), negative = moves tend to reverse
  (favours mean reversion). `|ac1| < 2/sqrt(n)` (`sig`) is indistinguishable
  from zero at ~95%. Shown for the whole period and for each half, since a
  sign that flips between halves is not something to build a strategy on.

Research only: nothing is persisted.

Run: python scripts/analyze_timeframes.py [--symbol BTCJPY]
"""

import argparse
import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.db.session import SessionLocal
from app.market_data.application.public_research import RESEARCH_EXCHANGE_CODE
from app.market_data.application.use_cases import SUPPORTED_TIMEFRAMES
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.trading.application.backtest_fill import fee_buffer
from app.trading.application.backtest_provisioning import load_final_candles
from sqlalchemy import select

ROUND_TRIP_COST = 2 * float(fee_buffer("binance", Decimal(1), Decimal(1)))


def _returns(closes: Sequence[float]) -> list[float]:
    return [current / previous - 1 for previous, current in zip(closes, closes[1:], strict=False)]


def _lag1_autocorrelation(returns: Sequence[float]) -> float:
    mean = statistics.fmean(returns)
    deviations = [r - mean for r in returns]
    variance = sum(d * d for d in deviations)
    if variance == 0:
        return 0.0
    covariance = sum(a * b for a, b in zip(deviations, deviations[1:], strict=False))
    return covariance / variance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCJPY")
    args = parser.parse_args()

    with SessionLocal() as db:
        instrument = db.scalar(
            select(Instrument)
            .join(Exchange, Exchange.id == Instrument.exchange_id)
            .where(Exchange.code == RESEARCH_EXCHANGE_CODE, Instrument.symbol == args.symbol)
        )
        if instrument is None:
            print(f"[NG] no {RESEARCH_EXCHANGE_CODE} instrument for {args.symbol}")
            return 1

        print(f"round-trip cost = {ROUND_TRIP_COST:.2%}")
        print(
            f"{'tf':>4} {'bars':>7} {'flat':>6} {'med|r|':>8} {'cost_x':>7} "
            f"{'ac1':>7} {'ac1_h1':>7} {'ac1_h2':>7} {'sig':>6}"
        )
        for timeframe in SUPPORTED_TIMEFRAMES:
            candles = load_final_candles(
                db, instrument.id, timeframe, datetime(2000, 1, 1, tzinfo=UTC), datetime.now(UTC)
            )
            returns = _returns([float(c.close) for c in candles])
            if len(returns) < 4:
                print(f"{timeframe:>4} too few candles ({len(candles)})")
                continue
            half = len(returns) // 2
            median_abs = statistics.median(abs(r) for r in returns)
            print(
                f"{timeframe:>4} {len(candles):>7} "
                f"{sum(r == 0 for r in returns) / len(returns):>6.1%} "
                f"{median_abs:>8.3%} {median_abs / ROUND_TRIP_COST:>7.2f} "
                f"{_lag1_autocorrelation(returns):>+7.3f} "
                f"{_lag1_autocorrelation(returns[:half]):>+7.3f} "
                f"{_lag1_autocorrelation(returns[half:]):>+7.3f} "
                f"{2 / math.sqrt(len(returns)):>6.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
