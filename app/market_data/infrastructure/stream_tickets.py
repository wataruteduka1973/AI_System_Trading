"""Short-lived, one-time WebSocket market-stream tickets (PyJWT-signed).

See docs/design/modules/realtime-market-data-stream.md section 4. Ticket
issuance (HTTP) and verification (WebSocket connect) both run inside the
same FastAPI process (see the same document's architecture decision), so a
process-local in-memory replay guard is sufficient. No shared storage
(database or otherwise) is needed for one-time-use enforcement, and no
schema change is required.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID, uuid4

import jwt

_ALGORITHM = "HS256"


class StreamTicketError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StreamTicketClaims:
    jti: str
    workspace_id: UUID
    exchange: str
    symbol: str
    timeframe: str
    expires_at: datetime


def issue_ticket(
    secret: str,
    *,
    workspace_id: UUID,
    exchange: str,
    symbol: str,
    timeframe: str,
    ttl_seconds: int,
) -> tuple[str, datetime]:
    """Sign a new one-time ticket. Returns (token, expires_at)."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    payload = {
        "jti": uuid4().hex,
        "workspace_id": str(workspace_id),
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, secret, algorithm=_ALGORITHM)
    return token, expires_at


class UsedTicketStore:
    """Process-local record of consumed ticket ids (``jti``), so a ticket can
    be redeemed at most once (RT-03). Entries are purged once their own
    expiry passes, so the store never grows without bound -- every ticket
    already carries a short TTL, and nothing is retained past it.
    """

    def __init__(self) -> None:
        self._used: dict[str, datetime] = {}

    def consume(self, jti: str, expires_at: datetime) -> bool:
        """Mark ``jti`` as used. Returns False if it was already used, or if
        the ticket's own expiry has already passed."""
        now = datetime.now(UTC)
        self._purge_expired(now)
        if expires_at <= now:
            return False
        if jti in self._used:
            return False
        self._used[jti] = expires_at
        return True

    def _purge_expired(self, now: datetime) -> None:
        expired = [jti for jti, expires_at in self._used.items() if expires_at <= now]
        for jti in expired:
            del self._used[jti]


@lru_cache
def get_default_used_ticket_store() -> UsedTicketStore:
    """Process-wide singleton, mirroring app.core.config.get_settings()'s
    lru_cache-backed singleton pattern."""
    return UsedTicketStore()


def verify_and_consume_ticket(
    secret: str, token: str, *, used_tickets: UsedTicketStore
) -> StreamTicketClaims:
    """Validate signature and expiry, then enforce one-time use.

    Raises StreamTicketError with a safe, stable code on any failure. Never
    includes the raw token or secret in the error message.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise StreamTicketError("ticket_expired", "Stream ticket has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise StreamTicketError("ticket_invalid", "Stream ticket is invalid") from exc
    try:
        claims = StreamTicketClaims(
            jti=payload["jti"],
            workspace_id=UUID(payload["workspace_id"]),
            exchange=payload["exchange"],
            symbol=payload["symbol"],
            timeframe=payload["timeframe"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise StreamTicketError("ticket_invalid", "Stream ticket is invalid") from exc
    if not used_tickets.consume(claims.jti, claims.expires_at):
        raise StreamTicketError("ticket_already_used", "Stream ticket has already been used")
    return claims
