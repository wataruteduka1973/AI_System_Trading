"""Keeps the public production prices current for paper-trading bots that use
them (docs/plans/paper-trading-live-data.md Unit 1; approval gate "Binance
Public履歴の併用" extended 2026-09-30 to cover paper-trading signal input --
prices only, never orders).

Run by the trading worker right before each evaluation pass, for exactly the
instruments and timeframes that running or paused bots need -- no separate
market-data worker. A 4h bot needs one request per closed bar, and
`refresh_public_klines` skips the request until one is due, so polling every
few seconds stays cheap.

A failed fetch is logged and skipped, not retried here: the bot simply finds
no new candle on this pass (its evaluation is idempotent per candle) and the
next pass tries again. If the outage lasts, the Risk Gate's data-delay check
stops new entries on the stale series.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exchanges.binance_public import BinancePublicApiError, BinancePublicClient
from app.market_data.application.public_research import (
    RESEARCH_EXCHANGE_CODE,
    refresh_public_klines,
)
from app.market_data.application.spread_tracking import record_spread_observation
from app.models.connections import Exchange
from app.models.instruments import Instrument
from app.models.market_data import InstrumentSpread
from app.models.strategy import TradingBot
from app.trading.application.backtest_replay import _HISTORY_WINDOW

logger = logging.getLogger(__name__)

INITIAL_BARS = _HISTORY_WINDOW
"""History fetched for an instrument with no stored bars: the same lookback a
signal generator and the ATR stop distance see in the backtest."""


async def refresh_public_prices_for_active_bots(
    db: Session, client: BinancePublicClient, *, now: datetime
) -> int:
    """Returns how many instrument/timeframe series were refreshed without error
    (including ones that were not yet due)."""
    targets = db.execute(
        select(Instrument, TradingBot.timeframe)
        .join(TradingBot, TradingBot.instrument_id == Instrument.id)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
        .where(
            TradingBot.actual_state.in_(("running", "paused")),
            Exchange.code == RESEARCH_EXCHANGE_CODE,
        )
        .distinct()
    ).all()
    refreshed = 0
    for instrument, timeframe in targets:
        try:
            await refresh_public_klines(
                db, client, instrument, timeframe, initial_bars=INITIAL_BARS, now=now
            )
        except BinancePublicApiError as exc:
            db.rollback()
            logger.warning(
                "public_price_refresh: %s %s failed (%s)",
                instrument.symbol,
                timeframe,
                type(exc).__name__,
            )
            continue
        refreshed += 1
    return refreshed


SPREAD_SOURCE = "binance_public_book_ticker"
SPREAD_REFRESH_INTERVAL = timedelta(seconds=30)
"""A spread observation younger than this is reused: the worker polls every few seconds,
the spread only feeds a simulated fill's slippage and the Risk Gate's stop distance, and
one Binance request (weight 4) per symbol set every 30s stays far below the rate limit."""


async def refresh_public_spreads_for_active_bots(
    db: Session, client: BinancePublicClient, *, now: datetime
) -> int:
    """Stores the latest bid/ask of every `binance_public` instrument an active bot trades,
    so `order_flow` and the Risk Gate see a spread for Binance as they do for OANDA
    (`fx.instrument_spread`). One request for all stale symbols. Returns how many
    instruments were written. A failed request raises `BinancePublicApiError`; the
    caller keeps the previous observation, which only makes the slippage less current."""
    instruments = db.scalars(
        select(Instrument)
        .join(TradingBot, TradingBot.instrument_id == Instrument.id)
        .join(Exchange, Exchange.id == Instrument.exchange_id)
        .where(
            TradingBot.actual_state.in_(("running", "paused")),
            Exchange.code == RESEARCH_EXCHANGE_CODE,
        )
        .distinct()
    ).all()
    stale = []
    for instrument in instruments:
        stored = db.get(InstrumentSpread, instrument.id)
        if stored is None or now - stored.observed_at >= SPREAD_REFRESH_INTERVAL:
            stale.append(instrument)
    if not stale:
        return 0
    quotes = await client.get_book_tickers([instrument.symbol for instrument in stale])
    written = 0
    for instrument in stale:
        quote = quotes.get(instrument.symbol)
        if quote is None:
            continue
        record_spread_observation(
            db,
            instrument.id,
            bid=quote[0],
            ask=quote[1],
            observed_at=now,
            source=SPREAD_SOURCE,
        )
        written += 1
    return written
