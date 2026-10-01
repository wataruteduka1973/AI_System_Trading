"""The candidate signal generators compared by the research scripts
(`scripts/run_walk_forward_report.py`, `scripts/compare_exit_policies.py`),
keyed by the name those scripts print. Each entry fixes its parameters up
front -- see each module's docstring for why they are textbook values chosen
before looking at results, not tuned ones.

None of these is wired into live/paper execution (`dummy_pipeline.py` still
uses `generate_dummy_signal`).
"""

from collections.abc import Sequence
from decimal import Decimal

from app.models.market_data import Candle
from app.trading.application.backtest_replay import BacktestSignalGenerator
from app.trading.application.donchian_breakout_signal import (
    DonchianSignalAction,
    generate_donchian_breakout_signal,
)
from app.trading.application.dummy_signal import generate_dummy_signal
from app.trading.application.ema_trend_signal import generate_ema_trend_signal
from app.trading.application.rsi_mean_reversion_signal import (
    RsiSignalAction,
    generate_rsi_mean_reversion_signal,
)


def _donchian_55_20(candles: Sequence[Candle]) -> DonchianSignalAction:
    return generate_donchian_breakout_signal(candles, entry_period=55, exit_period=20)


def _rsi2_10_70(candles: Sequence[Candle]) -> RsiSignalAction:
    return generate_rsi_mean_reversion_signal(
        candles, rsi_period=2, oversold=Decimal(10), exit_level=Decimal(70)
    )


RESEARCH_STRATEGIES: dict[str, BacktestSignalGenerator] = {
    "dummy_sma5": generate_dummy_signal,
    "ema_trend": generate_ema_trend_signal,
    "donchian_20_10": generate_donchian_breakout_signal,
    "donchian_55_20": _donchian_55_20,
    "rsi14_30_70": generate_rsi_mean_reversion_signal,
    "rsi2_10_70": _rsi2_10_70,
}
