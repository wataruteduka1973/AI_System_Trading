"""Donchian channel breakout (2026-09-30, per user request) -- the second
hand-crafted candidate after `ema_trend_signal.py`, evaluated with the rolling
walk-forward in `backtest_walk_forward.run_rolling_walk_forward`.

**Classic Turtle parameters, fixed in advance**: 20-bar entry / 10-bar exit
(Turtle System 1) is the default, and 55/20 (System 2) is the only other
variant evaluated. Both are the textbook values, chosen before looking at any
BTCJPY result -- tuning the periods against the same year of data would be the
overfitting trap the walk-forward exists to expose.

**Close-based breakout against the prior bars' high/low**: "buy" when this
bar's close is above the highest high of the `entry_period` bars before it,
"sell" when it is below the lowest low of the `exit_period` bars before it.
The current bar is excluded from its own channel (otherwise a close could
never exceed a channel that contains its own high). A close is used rather
than an intrabar touch because `backtest_fill` fills at the bar's close.

**Breakout-*event* detection, like `ema_trend_signal.py`**: a signal fires
only on the first bar of a breakout, not on every bar that stays outside the
channel. `backtest_replay.run_replay` treats a same-direction "buy" while long
as adding to the position, so re-signalling every bar of a trend would
pyramid on each bar; see `ema_trend_signal.py`'s docstring for the full
reasoning. A later breakout after at least one non-breakout bar is a new
event and may add to an open long.

**The exit is not trend-filtered**: as with `ema_trend_signal.py`, the
opposing "sell" is the only way a position exits in this engine (no simulated
stop-loss), so it must always be able to fire. On Binance a "sell" while flat
is simply denied by the Risk Gate's `binance_no_short` check.
"""

from collections.abc import Sequence

from app.models.market_data import Candle
from app.trading.application.signal_action import SignalAction

ENTRY_PERIOD = 20
EXIT_PERIOD = 10


def _breaks_above(candles: Sequence[Candle], index: int, period: int) -> bool:
    return candles[index].close > max(c.high for c in candles[index - period : index])


def _breaks_below(candles: Sequence[Candle], index: int, period: int) -> bool:
    return candles[index].close < min(c.low for c in candles[index - period : index])


def generate_donchian_breakout_signal(
    candles: Sequence[Candle],
    *,
    entry_period: int = ENTRY_PERIOD,
    exit_period: int = EXIT_PERIOD,
) -> SignalAction:
    """ "buy" on the first bar whose close exceeds the prior `entry_period` bars'
    highest high; "sell" on the first bar whose close falls below the prior
    `exit_period` bars' lowest low; "hold" otherwise, including while there are
    too few bars to evaluate both this bar and the one before it."""
    if len(candles) < max(entry_period, exit_period) + 2:
        return "hold"

    last = len(candles) - 1
    if _breaks_above(candles, last, entry_period) and not _breaks_above(
        candles, last - 1, entry_period
    ):
        return "buy"
    if _breaks_below(candles, last, exit_period) and not _breaks_below(
        candles, last - 1, exit_period
    ):
        return "sell"
    return "hold"
