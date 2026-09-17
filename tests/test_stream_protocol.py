"""Tests for app.market_data.infrastructure.stream_protocol. See
docs/design/modules/realtime-market-data-stream.md sections 5/7 and
docs/plans/realtime-market-data-stream.md (work units 5/6)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    CandleStreamEvent,
    StreamFeedKey,
)
from app.market_data.infrastructure.stream_protocol import (
    ResumeRequest,
    decide_resume,
    encode_event,
    encode_heartbeat,
    encode_stream_state,
)

WORKSPACE_ID = uuid4()
KEY = StreamFeedKey("binance", "BTCJPY", "1m")
FEED_STARTED_AT = datetime(2026, 9, 17, 0, 0, 0, tzinfo=UTC)


def _event(sequence: int, *, event_type: str = "provisional_update", **kwargs) -> CandleStreamEvent:
    defaults = dict(
        event_id=f"evt-{sequence}",
        exchange="binance",
        symbol="BTCJPY",
        timeframe="1m",
        sequence=sequence,
        event_type=event_type,
        open_time=datetime(2026, 9, 17, 0, sequence, tzinfo=UTC),
        ohlcv=OHLCV(Decimal("1"), Decimal("2"), Decimal("1"), Decimal("1.5"), Decimal("3")),
        source="binance_testnet",
        quality="provisional",
        reason_code=None,
    )
    defaults.update(kwargs)
    return CandleStreamEvent(**defaults)


# ---------------------------------------------------------------------------
# encode_event / encode_heartbeat / encode_stream_state
# ---------------------------------------------------------------------------


def test_encode_event_stamps_workspace_id_and_flattens_ohlcv() -> None:
    event = _event(3)
    encoded = encode_event(event, WORKSPACE_ID)
    assert encoded["workspace_id"] == str(WORKSPACE_ID)
    assert encoded["sequence"] == 3
    assert encoded["event_type"] == "provisional_update"
    assert encoded["ohlcv"] == {"open": "1", "high": "2", "low": "1", "close": "1.5", "volume": "3"}
    assert encoded["open_time"] == "2026-09-17T00:03:00+00:00"


def test_encode_event_handles_gap_notice_with_no_ohlcv() -> None:
    event = _event(
        4, event_type="gap_notice", open_time=None, ohlcv=None, quality=None,
        reason_code="binance_stream_disconnected",
    )
    encoded = encode_event(event, WORKSPACE_ID)
    assert encoded["ohlcv"] is None
    assert encoded["open_time"] is None
    assert encoded["reason_code"] == "binance_stream_disconnected"


def test_encode_heartbeat_repeats_last_sequence_without_ohlcv() -> None:
    encoded = encode_heartbeat(KEY, WORKSPACE_ID, 7, "binance_testnet")
    assert encoded["event_type"] == "heartbeat"
    assert encoded["sequence"] == 7
    assert encoded["ohlcv"] is None
    assert encoded["exchange"] == "binance"
    assert encoded["symbol"] == "BTCJPY"


def test_encode_stream_state_shape() -> None:
    encoded = encode_stream_state(FEED_STARTED_AT, "replayed")
    assert encoded == {
        "type": "stream_state",
        "feed_started_at": "2026-09-17T00:00:00+00:00",
        "resume": "replayed",
    }


# ---------------------------------------------------------------------------
# decide_resume -- RT-01/06/07/08
# ---------------------------------------------------------------------------


def test_fresh_connect_with_no_resume_state_replays_current_buffer_as_is() -> None:
    buffered = [_event(0), _event(1)]
    decision = decide_resume(
        ResumeRequest(None, None), feed_started_at=FEED_STARTED_AT, buffered_events=buffered
    )
    assert decision.mode == "fresh"
    assert decision.events_to_replay == buffered


def test_partial_resume_state_is_treated_as_fresh() -> None:
    decision = decide_resume(
        ResumeRequest(5, None), feed_started_at=FEED_STARTED_AT, buffered_events=[]
    )
    assert decision.mode == "fresh"


def test_feed_regenerated_since_last_connect_requires_gap_fill() -> None:
    older_started_at = FEED_STARTED_AT.replace(hour=0)
    newer_started_at = FEED_STARTED_AT.replace(hour=1)
    decision = decide_resume(
        ResumeRequest(3, older_started_at),
        feed_started_at=newer_started_at,
        buffered_events=[_event(0), _event(1)],
    )
    assert decision.mode == "gap_fill_required"
    assert decision.events_to_replay == []


def test_same_feed_with_full_ring_buffer_coverage_replays_only_missed_events() -> None:
    buffered = [_event(0), _event(1), _event(2), _event(3)]
    decision = decide_resume(
        ResumeRequest(1, FEED_STARTED_AT),
        feed_started_at=FEED_STARTED_AT,
        buffered_events=buffered,
    )
    assert decision.mode == "replayed"
    assert [event.sequence for event in decision.events_to_replay] == [2, 3]


def test_same_feed_with_nothing_missed_replays_empty_list() -> None:
    buffered = [_event(0), _event(1)]
    decision = decide_resume(
        ResumeRequest(1, FEED_STARTED_AT), feed_started_at=FEED_STARTED_AT, buffered_events=buffered
    )
    assert decision.mode == "replayed"
    assert decision.events_to_replay == []


def test_same_feed_but_ring_buffer_evicted_the_gap_requires_gap_fill() -> None:
    # Client last saw sequence 1, but the ring buffer's oldest surviving
    # entry is sequence 5 -- events 2-4 were evicted (maxlen exceeded).
    buffered = [_event(5), _event(6)]
    decision = decide_resume(
        ResumeRequest(1, FEED_STARTED_AT), feed_started_at=FEED_STARTED_AT, buffered_events=buffered
    )
    assert decision.mode == "gap_fill_required"
    assert decision.events_to_replay == []


def test_same_feed_with_empty_ring_buffer_replays_nothing_without_gap_fill() -> None:
    # Feed survived (e.g. grace-period reuse) but nothing has been
    # published since the client last connected -- not a gap, just quiet.
    decision = decide_resume(
        ResumeRequest(4, FEED_STARTED_AT), feed_started_at=FEED_STARTED_AT, buffered_events=[]
    )
    assert decision.mode == "replayed"
    assert decision.events_to_replay == []


def test_last_sequence_of_negative_one_means_never_seen_any_event() -> None:
    buffered = [_event(0), _event(1)]
    decision = decide_resume(
        ResumeRequest(-1, FEED_STARTED_AT),
        feed_started_at=FEED_STARTED_AT,
        buffered_events=buffered,
    )
    assert decision.mode == "replayed"
    assert [event.sequence for event in decision.events_to_replay] == [0, 1]
