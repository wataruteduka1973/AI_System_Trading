"""Unit 4 (docs/plans/horizon4-lite-backtest.md): the replay harness, exercised with
a small hand-built candle series and a deterministic fake signal generator (not
`generate_dummy_signal`, so the expected trade is easy to hand-verify) -- plus a
dedicated look-ahead-bias test using a spy generator.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
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


# ---- run_replay: warmup_bars ----


def test_warmup_bars_are_shown_as_history_but_never_traded_or_marked() -> None:
    # Closes equal their index, so the spy can tell which bars it was shown.
    candles = [_candle(Decimal(i + 1), i) for i in range(6)]
    history_lengths: list[int] = []
    first_close_seen: list[int] = []

    def spy(history: Sequence[Candle]) -> str:
        history_lengths.append(len(history))
        first_close_seen.append(int(history[0].close))
        return "buy"  # would open a position on any bar it is asked about

    result = replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=spy,  # type: ignore[arg-type]
        warmup_bars=4,
    )

    # Only the two post-warm-up bars are evaluated, each with the warm-up prefix
    # included in its history.
    assert history_lengths == [5, 6]
    assert first_close_seen == [1, 1]
    assert [t for t, _ in result.equity_curve] == [c.close_time for c in candles[4:]]
    assert result.ending_position is not None
    assert result.ending_position.average_entry_price == Decimal("5")


def test_warmup_bars_keep_the_look_ahead_guarantee() -> None:
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
        warmup_bars=8,
    )

    assert seen_max_close_by_call == list(range(8, 20))


def test_warmup_covering_every_candle_is_a_no_op() -> None:
    candles = [_candle(Decimal("100"), i) for i in range(3)]

    result = replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=lambda history: "buy",
        warmup_bars=3,
    )

    assert result.trades == []
    assert result.equity_curve == []
    assert result.ending_equity == Decimal("1000000")
    assert result.ending_position is None


def test_negative_warmup_bars_is_rejected() -> None:
    with pytest.raises(ValueError):
        replay.run_replay(
            [_candle(Decimal("100"), 0)],
            instrument=_instrument(),
            timeframe="1m",
            exchange_code="oanda",
            rules=gate.CONSERVATIVE_V1_RULES,
            initial_equity=Decimal("1000000"),
            warmup_bars=-1,
        )


# ---- run_replay: equity while a position is held ----


@pytest.mark.parametrize(("entry_action", "exit_close"), [("buy", "110"), ("sell", "90")])
def test_equity_while_holding_is_initial_equity_plus_unrealized_pnl(
    entry_action: str, exit_close: str
) -> None:
    # Regression: equity used to be cash + unrealized P&L, but cash has already paid
    # out (long) or received (short) the entry notional, so a held long read ~one
    # notional too low and a held short ~one notional too high.
    candles = [
        _candle(Decimal("100"), 0),
        _candle(Decimal("100"), 1),
        _candle(Decimal(exit_close), 2),
    ]

    def scripted_signal(history: Sequence[Candle]) -> str:
        return {1: entry_action, 2: "hold", 3: "hold"}[len(history)]

    result = replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",  # zero fees, so equity moves only with price
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=scripted_signal,  # type: ignore[arg-type]
    )

    assert result.ending_position is not None
    quantity = result.ending_position.quantity
    assert [equity for _, equity in result.equity_curve] == [
        Decimal("1000000"),
        Decimal("1000000"),  # held, price unchanged since entry
        Decimal("1000000") + 10 * quantity,  # held, price moved 10 in the position's favour
    ]
    assert result.ending_equity == Decimal("1000000") + 10 * quantity


def test_risk_gate_sees_cash_not_equity_as_available_cash_while_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = [_candle(Decimal("100"), 0), _candle(Decimal("110"), 1)]
    seen: list[gate.RiskState] = []
    real_evaluate = gate._evaluate_conservative_v1

    def spy(state: gate.RiskState) -> gate.PureRiskResult:
        seen.append(state)
        return real_evaluate(state)

    monkeypatch.setattr(gate, "_evaluate_conservative_v1", spy)

    replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",  # zero fees keep the arithmetic exact
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=lambda history: "buy",  # enter, then add to the long
    )

    assert len(seen) == 2
    assert seen[0].available_cash == seen[0].equity == Decimal("1000000")  # flat
    held = seen[1].existing_position_quantity
    assert held > 0
    assert seen[1].available_cash == Decimal("1000000") - 100 * held
    assert seen[1].equity == Decimal("1000000") + 10 * held


# ---- run_replay: protective exits (stop-loss / take-profit) ----
#
# `_stop_distance` is pinned to 5 so a long entered at 100 has its stop at 95 and
# its take-profit at 100 + 5 * min_reward_risk (2.0) = 110.


def _ohlc(i: int, open_: str, high: str, low: str, close: str) -> Candle:
    t = datetime(2026, 9, 21, 0, 0, tzinfo=UTC) + timedelta(minutes=i)
    return Candle(
        id=uuid4(),
        instrument_id=uuid4(),
        timeframe="1m",
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        source="test",
        is_final=True,
    )


def _run_with_exits(
    candles: list[Candle],
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry: str = "buy",
    exit_policy: replay.ExitPolicy = "stop_and_target",
) -> replay.ReplayResult:
    monkeypatch.setattr(gate, "_stop_distance", lambda *args: Decimal(5))

    def scripted_signal(history: Sequence[Candle]) -> str:
        return entry if len(history) == 1 else "hold"

    return replay.run_replay(
        candles,
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="oanda",  # zero fees, and shorts are allowed
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=Decimal("1000000"),
        signal_generator=scripted_signal,  # type: ignore[arg-type]
        exit_policy=exit_policy,
    )


def test_a_long_is_stopped_out_at_its_stop_price(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "99", "101", "94", "96")]
    result = _run_with_exits(candles, monkeypatch)

    assert result.ending_position is None
    [trade] = result.trades
    assert trade.exit_price == Decimal("95")
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_time == candles[1].close_time


def test_a_gap_through_the_stop_fills_at_the_open(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "90", "91", "88", "89")]
    [trade] = _run_with_exits(candles, monkeypatch).trades
    assert trade.exit_price == Decimal("90")
    assert trade.exit_reason == "stop_loss"


def test_a_long_takes_profit_at_its_target(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "101", "111", "99", "108")]
    [trade] = _run_with_exits(candles, monkeypatch).trades
    assert trade.exit_price == Decimal("110")
    assert trade.exit_reason == "take_profit"


def test_when_one_bar_reaches_both_the_stop_is_assumed_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "100", "111", "94", "100")]
    [trade] = _run_with_exits(candles, monkeypatch).trades
    assert trade.exit_price == Decimal("95")
    assert trade.exit_reason == "stop_loss"


def test_the_entry_bar_itself_never_triggers_an_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    # The entry fills at bar 0's close; bar 0's own low happened before that.
    candles = [_ohlc(0, "100", "100", "80", "100"), _ohlc(1, "100", "101", "99", "100")]
    result = _run_with_exits(candles, monkeypatch)
    assert result.trades == []
    assert result.ending_position is not None


def test_a_short_is_stopped_out_above_its_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "101", "106", "99", "104")]
    [trade] = _run_with_exits(candles, monkeypatch, entry="sell").trades
    assert trade.exit_price == Decimal("105")
    assert trade.exit_reason == "stop_loss"


def test_signal_exit_policy_ignores_stops_and_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "99", "101", "94", "96")]
    result = _run_with_exits(candles, monkeypatch, exit_policy="signal")
    assert result.trades == []
    assert result.ending_position is not None


def test_signal_closes_are_labelled_as_signal_exits() -> None:
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
        signal_generator=scripted_signal,  # type: ignore[arg-type]
    )
    [trade] = result.trades
    assert trade.exit_reason == "signal"


# ---- run_replay: every fee and every round trip is attributed to a trade ----


def _binance_round_trip(
    closes: list[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[replay.ReplayResult, Decimal]:
    monkeypatch.setattr(gate, "_stop_distance", lambda *args: Decimal(5))

    def scripted_signal(history: Sequence[Candle]) -> str:
        return {1: "buy", len(closes): "sell"}.get(len(history), "hold")

    initial = Decimal("1000000")
    result = replay.run_replay(
        [_candle(Decimal(c), i) for i, c in enumerate(closes)],
        instrument=_instrument(),
        timeframe="1m",
        exchange_code="binance",  # 0.1% fee on both the entry and the exit
        rules=gate.CONSERVATIVE_V1_RULES,
        initial_equity=initial,
        signal_generator=scripted_signal,  # type: ignore[arg-type]
    )
    return result, initial


def test_a_trade_carries_both_its_entry_and_exit_fees(monkeypatch: pytest.MonkeyPatch) -> None:
    result, initial = _binance_round_trip(["100", "105", "110"], monkeypatch)

    [trade] = result.trades
    entry_fee = Decimal("100") * trade.quantity * Decimal("0.001")
    exit_fee = Decimal("110") * trade.quantity * Decimal("0.001")
    assert trade.fees == entry_fee + exit_fee
    # Flat at the end: closed trades' net P&L must account for the whole equity change.
    assert trade.realized_pnl - trade.fees == result.ending_equity - initial


def test_a_break_even_round_trip_is_still_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    result, initial = _binance_round_trip(["100", "100", "100"], monkeypatch)

    [trade] = result.trades
    assert trade.realized_pnl == 0
    assert trade.fees > 0
    assert -trade.fees == result.ending_equity - initial


def test_stop_loss_policy_lets_a_winner_run_past_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "101", "111", "99", "108")]
    result = _run_with_exits(candles, monkeypatch, exit_policy="stop_loss")
    assert result.trades == []
    assert result.ending_position is not None


def test_stop_loss_policy_still_stops_out(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = [_ohlc(0, "100", "100", "100", "100"), _ohlc(1, "99", "101", "94", "96")]
    [trade] = _run_with_exits(candles, monkeypatch, exit_policy="stop_loss").trades
    assert trade.exit_price == Decimal("95")
    assert trade.exit_reason == "stop_loss"
