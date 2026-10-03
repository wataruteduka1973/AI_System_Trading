"""EMA(12/26) crossover with an EMA(200) trend filter -- the first hand-crafted
technical-indicator candidate strategy (2026-09-28, per user decision: compare
concrete indicator strategies against the `dummy_signal.py` SMA5 baseline on
real BTCJPY data before deciding what to develop further -- see
`app/trading/application/dummy_signal.py`'s own docstring for what that
baseline is and why it exists).

**Real backtest evidence against the SMA5 baseline** (real production BTCJPY,
1 year, 15m/1h/4h/1d -- see `docs/architecture/architecture-alignment-and-long-term-roadmap.md`
for the full comparison) showed SMA5 losing money even before fees on every
timeframe tested: not a fee/frequency problem, a negative-edge-per-trade
problem. This signal targets a larger, trend-confirmed move per trade instead
of reacting to single-bar noise.

**Only "buy" is trend-filtered, not "sell"**: once a position is open, an
opposite crossover must always be able to close it regardless of the EMA(200)
trend, since `backtest_replay.run_replay`'s "opposing signal" branch is the
only way a position ever exits in this engine (there is no simulated
stop-loss/take-profit -- see that module's docstring). Filtering the exit too
would risk holding a losing position indefinitely whenever the trend filter
disagrees with the exit signal.

**Crossover-*event* detection, not level comparison**: this returns "buy"/"sell"
only on the bar where the fast/slow EMA relationship actually flips, not on
every bar the flipped relationship persists. `backtest_replay.run_replay`
issues a real fill for every non-"hold" signal that isn't closing an opposing
position -- including a same-direction "buy" while already long, which
`backtest_fill.apply_fill_to_position`'s same-direction branch treats as
*adding* to the position. A level-comparison design would therefore
re-buy every single bar a trend persists, inflating position size and fee
drag while `backtest_metrics.compute_metrics`'s `net_pnl`/`total_fees` (which
only sum realized/closing legs, per `backtest_replay._apply_and_record`)
would not even reflect it. Emitting "buy"/"sell" only on the crossing bar
avoids this entirely and matches how an EMA-crossover strategy is
conventionally traded.

**Needs a longer lookback than `dummy_signal.py`'s SMA5**: EMA(200) needs at
least 200 candles to seed, plus one more to detect a crossover (comparing this
bar's fast/slow relationship to the previous bar's). `backtest_replay.py`'s
`_HISTORY_WINDOW` (= `risk_gate._ATR_HISTORY_CANDLES`) was raised from 100 to
260 for this reason -- see that constant's own docstring.
"""

from collections.abc import Sequence

from app.market_data.application.indicators import exponential_moving_average
from app.models.market_data import Candle
from app.trading.application.signal_action import SignalAction

FAST_PERIOD = 12
SLOW_PERIOD = 26
TREND_PERIOD = 200


def generate_ema_trend_signal(
    candles: Sequence[Candle],
    *,
    fast_period: int = FAST_PERIOD,
    slow_period: int = SLOW_PERIOD,
    trend_period: int = TREND_PERIOD,
) -> SignalAction:
    """ "buy" on the bar the fast EMA crosses above the slow EMA, provided the
    latest close is above the EMA(trend_period) trend filter; "sell" on the
    bar the fast EMA crosses below the slow EMA, regardless of the trend
    filter; "hold" otherwise (including whenever there isn't enough history
    for all three EMAs plus a one-bar-earlier comparison)."""
    if len(candles) < trend_period + 1:
        return "hold"

    fast_now = exponential_moving_average(candles, fast_period)
    slow_now = exponential_moving_average(candles, slow_period)
    trend_now = exponential_moving_average(candles, trend_period)
    fast_prev = exponential_moving_average(candles[:-1], fast_period)
    slow_prev = exponential_moving_average(candles[:-1], slow_period)
    if None in (fast_now, slow_now, trend_now, fast_prev, slow_prev):
        return "hold"
    assert fast_now is not None
    assert slow_now is not None
    assert trend_now is not None
    assert fast_prev is not None
    assert slow_prev is not None

    crossed_up = fast_prev <= slow_prev and fast_now > slow_now
    crossed_down = fast_prev >= slow_prev and fast_now < slow_now

    if crossed_up and candles[-1].close > trend_now:
        return "buy"
    if crossed_down:
        return "sell"
    return "hold"
