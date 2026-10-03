"""Donchian(55/20) breakout entries gated by extra indicators -- the confluence
filters of docs/plans/confluence-filters.md (2026-09-30, per user request:
trade only when several indicators agree, with not losing money first).

**Filters gate "buy" only, never "sell"**: the opposing signal is this engine's
only exit besides the stop-loss, so a filter must never keep a position open.

**Indicators are precomputed once, not per bar**: a walk-forward that selects
among 12 filter combinations replays every combination over every fold's
train and test windows, and recomputing EMA(200)/ATR/RSI from a 260-bar window
on every bar would take hours. `build_indicator_table` instead walks the whole
series once, updating each indicator recursively from the bars seen so far,
so each bar's snapshot depends only on that bar and earlier ones -- the same
values the existing `indicators.py` functions give for that bar's own prefix
(see the tests). Values are floats: they only ever feed comparisons.

A bar that is missing from the table, or whose indicator is not ready yet, is
treated as failing any filter that needs it -- the conservative choice.
"""

import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models.market_data import Candle
from app.trading.application.backtest_replay import BacktestSignalGenerator
from app.trading.application.donchian_breakout_signal import generate_donchian_breakout_signal
from app.trading.application.signal_action import SignalAction

TREND_PERIOD = 200
ATR_PERIOD = 14
RSI_PERIOD = 14
VOLATILITY_WINDOW = 260
MOMENTUM_CAP = 75.0

VolatilityFilter = Literal["any", "calm", "expanding"]


@dataclass(frozen=True)
class IndicatorSnapshot:
    trend_ema: float | None
    atr_pct: float | None
    """ATR as a fraction of the bar's close, so it is comparable across price levels."""
    atr_pct_median: float | None
    """Median `atr_pct` over the last `volatility_window` bars, this one included."""
    rsi: float | None


def build_indicator_table(
    candles: Sequence[Candle],
    *,
    trend_period: int = TREND_PERIOD,
    atr_period: int = ATR_PERIOD,
    rsi_period: int = RSI_PERIOD,
    volatility_window: int = VOLATILITY_WINDOW,
) -> dict[datetime, IndicatorSnapshot]:
    """Snapshot per `open_time`, each computed only from that bar and earlier
    ones: EMA seeded by the first `trend_period` closes, ATR and RSI with
    Wilder's smoothing seeded by their first `period` values -- matching
    `indicators.py` applied to the series' prefix."""
    table: dict[datetime, IndicatorSnapshot] = {}
    closes: list[float] = []
    true_ranges: list[float] = []
    gains: list[float] = []
    losses: list[float] = []
    ema: float | None = None
    atr: float | None = None
    avg_gain: float | None = None
    avg_loss: float | None = None
    recent_atr_pcts: deque[float] = deque(maxlen=volatility_window)
    multiplier = 2 / (trend_period + 1)

    for candle in candles:
        close, high, low = float(candle.close), float(candle.high), float(candle.low)
        if closes:
            previous = closes[-1]
            true_ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
            change = close - previous
            gains.append(max(change, 0.0))
            losses.append(max(-change, 0.0))
        closes.append(close)

        if len(closes) == trend_period:
            ema = sum(closes) / trend_period
        elif ema is not None:
            ema = (close - ema) * multiplier + ema

        if len(true_ranges) == atr_period:
            atr = sum(true_ranges) / atr_period
        elif atr is not None:
            atr = (atr * (atr_period - 1) + true_ranges[-1]) / atr_period

        if len(gains) == rsi_period:
            avg_gain, avg_loss = sum(gains) / rsi_period, sum(losses) / rsi_period
        elif avg_gain is not None and avg_loss is not None:
            avg_gain = (avg_gain * (rsi_period - 1) + gains[-1]) / rsi_period
            avg_loss = (avg_loss * (rsi_period - 1) + losses[-1]) / rsi_period

        atr_pct = atr / close if atr is not None and close else None
        if atr_pct is not None:
            recent_atr_pcts.append(atr_pct)
        table[candle.open_time] = IndicatorSnapshot(
            trend_ema=ema,
            atr_pct=atr_pct,
            atr_pct_median=(
                statistics.median(recent_atr_pcts)
                if len(recent_atr_pcts) == volatility_window
                else None
            ),
            rsi=_rsi(avg_gain, avg_loss),
        )
    return table


def _rsi(avg_gain: float | None, avg_loss: float | None) -> float | None:
    """Same edge cases as `indicators.relative_strength_index`."""
    if avg_gain is None or avg_loss is None:
        return None
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


@dataclass(frozen=True)
class ConfluenceFilters:
    trend: bool = False
    """Buy only while the close is above EMA(`TREND_PERIOD`)."""
    volatility: VolatilityFilter = "any"
    """`calm`: ATR% at or below its recent median; `expanding`: above it."""
    momentum_cap: bool = False
    """Buy only while RSI is below `MOMENTUM_CAP` (do not chase an overheated move)."""

    @property
    def name(self) -> str:
        parts = [
            "trend" if self.trend else "",
            f"vol-{self.volatility}" if self.volatility != "any" else "",
            f"rsi<{MOMENTUM_CAP:g}" if self.momentum_cap else "",
        ]
        return "+".join(p for p in parts if p) or "no-filters"

    def allows_buy(self, close: float, snapshot: IndicatorSnapshot | None) -> bool:
        if self == NO_FILTERS:
            return True
        if snapshot is None:
            return False
        if self.trend and (snapshot.trend_ema is None or close <= snapshot.trend_ema):
            return False
        if self.volatility != "any":
            if snapshot.atr_pct is None or snapshot.atr_pct_median is None:
                return False
            calm = snapshot.atr_pct <= snapshot.atr_pct_median
            if calm != (self.volatility == "calm"):
                return False
        return not (self.momentum_cap and (snapshot.rsi is None or snapshot.rsi >= MOMENTUM_CAP))


NO_FILTERS = ConfluenceFilters()

ALL_FILTER_COMBINATIONS: tuple[ConfluenceFilters, ...] = tuple(
    ConfluenceFilters(trend=trend, volatility=volatility, momentum_cap=momentum_cap)
    for trend in (False, True)
    for volatility in ("any", "calm", "expanding")
    for momentum_cap in (False, True)
)
"""All 12 combinations, `NO_FILTERS` first (it wins ties in selection)."""


def make_confluence_signal(
    table: dict[datetime, IndicatorSnapshot],
    filters: ConfluenceFilters,
    *,
    entry_period: int = 55,
    exit_period: int = 20,
) -> BacktestSignalGenerator:
    def generate(candles: Sequence[Candle]) -> SignalAction:
        action = generate_donchian_breakout_signal(
            candles, entry_period=entry_period, exit_period=exit_period
        )
        if action != "buy":
            return action
        last = candles[-1]
        return "buy" if filters.allows_buy(float(last.close), table.get(last.open_time)) else "hold"

    generate.__name__ = f"donchian_{entry_period}_{exit_period}[{filters.name}]"
    return generate
