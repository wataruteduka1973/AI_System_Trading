"""RSI mean reversion with an EMA(200) trend filter (2026-09-30, per user
request) -- the third hand-crafted candidate after `ema_trend_signal.py` and
`donchian_breakout_signal.py`, and the first *counter-trend* entry: it buys
short-term weakness inside a longer uptrend instead of buying strength.

**Two textbook variants, fixed in advance**: RSI(14) 30/70 (Wilder's own
levels) is the default, and RSI(2) 10/70 (Connors' short-period variant) is the
only other one evaluated. Both use EMA(200) as the trend filter, matching
`ema_trend_signal.py`. Chosen before looking at any BTCJPY result; tuning them
against the same year of data would be the overfitting trap the rolling
walk-forward exists to expose.

**Crossing-*event* detection, like the other candidates**: "buy" only on the
bar RSI first drops below `oversold` (and only if that bar's close is above the
trend EMA), "sell" only on the bar RSI first rises above `exit_level`.
`backtest_replay.run_replay` treats a repeated "buy" while long as adding to the
position; see `ema_trend_signal.py`'s docstring.

**The exit is not trend-filtered**, for the same reason as the other
candidates: the opposing "sell" is the only exit this engine has.

**Known structural risk -- no stop-loss**: `run_replay` simulates no
stop-loss/take-profit, so a dip bought just before a real trend reversal is
held until RSI next rises above `exit_level`, however far price falls first.
Mean reversion is the strategy family most exposed to this; the backtest
reflects it rather than hiding it.
"""

from collections.abc import Sequence
from decimal import Decimal

from app.market_data.application.indicators import (
    exponential_moving_average,
    relative_strength_index,
)
from app.models.market_data import Candle
from app.trading.application.signal_action import SignalAction

RSI_PERIOD = 14
OVERSOLD = Decimal(30)
EXIT_LEVEL = Decimal(70)
TREND_PERIOD = 200


def generate_rsi_mean_reversion_signal(
    candles: Sequence[Candle],
    *,
    rsi_period: int = RSI_PERIOD,
    oversold: Decimal = OVERSOLD,
    exit_level: Decimal = EXIT_LEVEL,
    trend_period: int = TREND_PERIOD,
) -> SignalAction:
    """ "buy" on the bar RSI(`rsi_period`) first drops below `oversold`, provided
    the close is above EMA(`trend_period`); "sell" on the bar RSI first rises
    above `exit_level`, regardless of trend; "hold" otherwise, including while
    there is too little history for the EMA or for RSI on both this bar and the
    one before it."""
    if len(candles) < max(rsi_period + 2, trend_period):
        return "hold"

    rsi_now = relative_strength_index(candles, rsi_period)
    rsi_prev = relative_strength_index(candles[:-1], rsi_period)
    trend = exponential_moving_average(candles, trend_period)
    if rsi_now is None or rsi_prev is None or trend is None:
        return "hold"

    if rsi_prev >= oversold > rsi_now and candles[-1].close > trend:
        return "buy"
    if rsi_prev <= exit_level < rsi_now:
        return "sell"
    return "hold"
