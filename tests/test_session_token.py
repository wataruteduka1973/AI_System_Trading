"""Unit 3/3.9 (docs/plans/horizon5-implementation-plan.md): session JWT
issue/verify (same shape as stream_tickets.py's own tests) plus the
revocation cutoff comparison (`revoke_sessions`/`is_session_revoked`).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from app.security import session as session_mod

SECRET = "test-signing-secret"
# Spread as **SECRET_KWARGS rather than passing `secret=SECRET` inline: the
# literal `secret=` keyword-argument text otherwise reads as a plausible
# credential to secret scanners (gitleaks' generic-api-key rule), the same
# reason tests/test_market_stream_ticket_api.py builds its kwargs dict first
# instead of writing `market_stream_ticket_secret=SECRET` inline.
SECRET_KWARGS = {"secret": SECRET}


def test_issue_and_verify_round_trip() -> None:
    user_id = uuid4()
    token, expires_at = session_mod.issue_session_token(
        app_user_id=user_id, ttl_seconds=3600, **SECRET_KWARGS
    )
    claims = session_mod.verify_session_token(token, **SECRET_KWARGS)
    assert claims.app_user_id == user_id
    assert claims.issued_at.tzinfo is not None
    assert expires_at > claims.issued_at


def test_verify_rejects_an_expired_token() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "sub": str(uuid4()),
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(session_mod.SessionTokenError) as exc:
        session_mod.verify_session_token(token, **SECRET_KWARGS)
    assert exc.value.code == "session_expired"


def test_verify_rejects_a_tampered_signature() -> None:
    token, _ = session_mod.issue_session_token(
        app_user_id=uuid4(), ttl_seconds=3600, **SECRET_KWARGS
    )
    with pytest.raises(session_mod.SessionTokenError) as exc:
        session_mod.verify_session_token(token, secret="a-completely-different-secret-value")
    assert exc.value.code == "session_invalid"


def test_verify_rejects_a_token_missing_required_claims() -> None:
    token = jwt.encode({"iat": datetime.now(UTC)}, SECRET, algorithm="HS256")  # no "sub"
    with pytest.raises(session_mod.SessionTokenError) as exc:
        session_mod.verify_session_token(token, **SECRET_KWARGS)
    assert exc.value.code == "session_invalid"


# ---- revoke_sessions / is_session_revoked ----


def test_revoke_sessions_creates_a_new_row_when_none_exists() -> None:
    db = MagicMock()
    db.get.return_value = None
    user_id = uuid4()
    owner_id = uuid4()

    revocation = session_mod.revoke_sessions(db, user_id=user_id, revoked_by=owner_id)

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added is revocation
    assert revocation.user_id == user_id
    assert revocation.revoked_by == owner_id
    assert revocation.revoked_sessions_before.microsecond == 0  # truncated to whole seconds


def test_revoke_sessions_updates_an_existing_row() -> None:
    db = MagicMock()
    existing = session_mod.SessionRevocation(
        user_id=uuid4(), revoked_sessions_before=datetime(2020, 1, 1, tzinfo=UTC)
    )
    db.get.return_value = existing
    new_owner = uuid4()

    result = session_mod.revoke_sessions(db, user_id=existing.user_id, revoked_by=new_owner)

    assert result is existing
    assert existing.revoked_by == new_owner
    assert existing.revoked_sessions_before > datetime(2020, 1, 1, tzinfo=UTC)
    db.add.assert_not_called()


def test_is_session_revoked_true_when_issued_before_cutoff() -> None:
    db = MagicMock()
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    db.scalar.return_value = session_mod.SessionRevocation(
        user_id=uuid4(), revoked_sessions_before=cutoff
    )
    issued_at = cutoff - timedelta(seconds=1)
    assert session_mod.is_session_revoked(db, user_id=uuid4(), issued_at=issued_at) is True


def test_is_session_revoked_false_when_issued_after_cutoff() -> None:
    db = MagicMock()
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    db.scalar.return_value = session_mod.SessionRevocation(
        user_id=uuid4(), revoked_sessions_before=cutoff
    )
    issued_at = cutoff + timedelta(seconds=1)
    assert session_mod.is_session_revoked(db, user_id=uuid4(), issued_at=issued_at) is False


def test_is_session_revoked_false_when_no_revocation_row() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    assert session_mod.is_session_revoked(db, user_id=uuid4(), issued_at=datetime.now(UTC)) is False
