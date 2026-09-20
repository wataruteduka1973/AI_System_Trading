"""Unit 4 (docs/plans/horizon4-lite-backtest.md): the replay harness, exercised with
a small hand-built candle series and a deterministic fake signal generator (not
`generate_dummy_signal`, so the expected trade is easy to hand-verify) -- plus a
dedicated look-ahead-bias test using a spy generator.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.instruments import Instrument
from app.models.market_data import Candle
from app.trading.application import backtest_replay as replay
from app.trading.application import risk_gate as gate


def _instrument(**overrides: object) -> Instrument:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        exchange_id=uuid4(),
        market_id=uuid4(),
        symbol="USD_JPY",
        base_asset="USD",
        quote_asset="JPY",
        price_scale=3,
        quantity_scale=0,
        tick_size=Decimal("0.001"),
        step_size=Decimal("1"),
        min_quantity=None,
        max_quantity=None,
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def _candle(close: Decimal, i: int) -> Candle:
    t = datetime(2026, 9, 21, 0, 0, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        source="test",
        is_final=True,
    )


# ---- run_replay: a known buy-then-close round trip ----


def test_run_replay_produces_one_trade_for_a_buy_then_opposing_sell() -> None:
    candles = [_candle(Decimal("100"), 0), _candle(Decimal("100"), 1), _candle(Decimal("110"), 2)]

    def scripted_signal(history: Sequence[Candle]) -> str:
        return {1: "buy", 2: "hold", 3: "sell"}[len(history)]

    result = replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        spread=Decimal(0),
        signal_generator=scripted_signal,  # type: ignore[arg-type]
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.sequence_no == 1
    assert trade.side == "sell"
    assert trade.entry_price == Decimal("100")
    assert trade.exit_price == Decimal("110")
    assert trade.entry_time == candles[0].close_time
    assert trade.exit_time == candles[2].close_time
    # Whatever quantity conservative-v1 approved on bar 0, the round trip must be
    # internally consistent: realized_pnl == (exit - entry) * quantity, and closing
    # a long fully (dote-gating always closes the exact held quantity) must leave no
    # position and account for the entire equity change (fees were 0 throughout).
    assert trade.realized_pnl == (trade.exit_price - trade.entry_price) * trade.quantity
    assert result.ending_position is None
    assert result.ending_equity == Decimal("1000000") + trade.realized_pnl


def test_run_replay_denies_when_equity_is_zero() -> None:
    candles = [_candle(Decimal("100"), 0)]
    result = replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("0"),
        signal_generator=lambda history: "buy",
    )
    assert result.trades == []
    assert result.ending_position is None
    assert result.ending_equity == Decimal("0")


def test_run_replay_with_no_candles_is_a_no_op() -> None:
    result = replay.run_replay(
        [],
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000"),
    )
    assert result == replay.ReplayResult(
        trades=[], ending_equity=Decimal("1000"), ending_position=None, equity_curve=[]
    )


# ---- look-ahead bias ----


def test_run_replay_never_shows_the_signal_generator_a_future_candle() -> None:
    # Each candle's close is its own index -- a spy generator can therefore prove
    # look-ahead just by checking the *value* of the closes it was shown.
    candles = [_candle(Decimal(i), i) for i in range(20)]
    seen_max_close_by_call: list[int] = []

    def spy(history: Sequence[Candle]) -> str:
        seen_max_close_by_call.append(int(max(c.close for c in history)))
        return "hold"

    replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=spy,  # type: ignore[arg-type]
    )

    assert len(seen_max_close_by_call) == 20
    for bar_index, max_close_seen in enumerate(seen_max_close_by_call):
        assert max_close_seen == bar_index, (
            f"bar {bar_index} saw a candle with close={max_close_seen}, which is from a later bar"
        )
