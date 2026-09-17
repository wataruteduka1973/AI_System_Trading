"""Unit tests for the stream ticket infrastructure (issuance, signature/expiry
verification, and one-time-use enforcement). See
docs/design/modules/realtime-market-data-stream.md section 4."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from app.market_data.infrastructure.stream_tickets import (
    StreamTicketError,
    UsedTicketStore,
    issue_ticket,
    verify_and_consume_ticket,
)

SECRET = "test-signing-secret"


def _issue(**overrides):
    kwargs = {
        "workspace_id": uuid4(),
        "exchange": "oanda",
        "symbol": "USD_JPY",
        "timeframe": "1m",
        "ttl_seconds": 60,
    }
    kwargs.update(overrides)
    return issue_ticket(SECRET, **kwargs)


def test_valid_ticket_round_trips_with_correct_claims():
    workspace_id = uuid4()
    token, expires_at = _issue(workspace_id=workspace_id, exchange="binance", symbol="BTCJPY")

    claims = verify_and_consume_ticket(SECRET, token, used_tickets=UsedTicketStore())

    assert claims.workspace_id == workspace_id
    assert claims.exchange == "binance"
    assert claims.symbol == "BTCJPY"
    assert claims.timeframe == "1m"
    assert claims.expires_at == expires_at


def test_ticket_cannot_be_used_twice():
    token, _ = _issue()
    store = UsedTicketStore()

    verify_and_consume_ticket(SECRET, token, used_tickets=store)

    with pytest.raises(StreamTicketError, match="already been used") as exc_info:
        verify_and_consume_ticket(SECRET, token, used_tickets=store)
    assert exc_info.value.code == "ticket_already_used"


def test_expired_ticket_is_rejected():
    token, _ = _issue(ttl_seconds=-1)

    with pytest.raises(StreamTicketError, match="expired") as exc_info:
        verify_and_consume_ticket(SECRET, token, used_tickets=UsedTicketStore())
    assert exc_info.value.code == "ticket_expired"


def test_tampered_signature_is_rejected():
    token, _ = _issue()

    with pytest.raises(StreamTicketError, match="invalid") as exc_info:
        verify_and_consume_ticket("wrong-secret", token, used_tickets=UsedTicketStore())
    assert exc_info.value.code == "ticket_invalid"


def test_malformed_token_is_rejected():
    with pytest.raises(StreamTicketError, match="invalid") as exc_info:
        verify_and_consume_ticket(SECRET, "not-a-jwt", used_tickets=UsedTicketStore())
    assert exc_info.value.code == "ticket_invalid"


def test_token_missing_required_claims_is_rejected():
    now = datetime.now(UTC)
    token = jwt.encode(
        {"jti": "abc", "iat": now, "exp": now + timedelta(seconds=60)},
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(StreamTicketError, match="invalid") as exc_info:
        verify_and_consume_ticket(SECRET, token, used_tickets=UsedTicketStore())
    assert exc_info.value.code == "ticket_invalid"


def test_used_ticket_store_purges_expired_entries_instead_of_growing_forever():
    store = UsedTicketStore()
    now = datetime.now(UTC)

    assert store.consume("already-expired", now - timedelta(seconds=1)) is False
    assert store.consume("still-valid", now + timedelta(seconds=60)) is True
    # The expired entry above was never retained, so it never counted against
    # this second ticket's own jti (no false "already used" positive).
    assert store.consume("still-valid-2", now + timedelta(seconds=60)) is True
    assert len(store._used) == 2
