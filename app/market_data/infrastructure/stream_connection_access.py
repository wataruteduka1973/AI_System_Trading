"""Resolves the exchange-connection credentials needed to open a *shared*
upstream feed connection (OANDA PricingStream / Binance kline_socket).

See docs/plans/realtime-market-data-stream.md (work unit 5) and section 4
of docs/design/modules/realtime-market-data-stream.md.

Deliberately keyed by (workspace_id, exchange) alone, NOT instrument_id: a
stream ticket (work unit 2, app.market_data.infrastructure.stream_tickets)
carries only workspace_id/exchange/symbol/timeframe -- it never carries an
instrument_id -- and the credentials themselves belong to a workspace's
exchange connection, not to any one instrument (the same OANDA account /
Binance API key serves every instrument reachable through that connection).
This is why this module does not reuse
app.market_data.infrastructure.page_access.PageAccess.resolve (work unit
2/3), which is keyed by instrument_id and is used only by the ticket-
issuance HTTP endpoint, never by the WS termination layer (work unit 5).

This module is invoked again at WS-connect time (not only once, earlier,
at ticket issuance), which is what satisfies design doc section 4's
"Workspace境界はticket発行時とWS接続時の両方で確認する": if a Workspace's
connection was invalidated, or its account selection changed, after a
ticket was already issued, this lookup fails and the WS connection is
refused (RT-12) even though the ticket's own signature/expiry/one-time-use
check (work unit 2) still passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.market_data.infrastructure.access import (
    MarketDataAccessError,
    ensure_sandbox_endpoint,
    load_credentials,
    selected_account_statement,
)
from app.models.connections import Exchange, ExchangeConnection, ExternalAccount

_SUPPORTED_EXCHANGES = ("oanda", "binance")


class SecretReader(Protocol):
    """The subset of app.services.secrets.LocalEncryptedSecretStore this
    module needs -- expressed as a Protocol so tests can supply a fake
    without needing the real (compiled/cryptography-backed) store."""

    def get(self, secret_ref: str) -> dict[str, str]: ...

    def decrypt_text(self, value: str) -> str: ...


@dataclass(frozen=True)
class StreamConnectionCredentials:
    """What a FeedStarter needs to open one upstream connection. Exactly one
    credential pair is populated, matching `exchange`: (token, account_id)
    for OANDA, or (api_key, secret_key) for Binance."""

    exchange: str
    base_url: str
    token: str | None = None
    account_id: str | None = None
    api_key: str | None = None
    secret_key: str | None = None


def resolve_stream_connection_credentials(
    db: Session,
    secrets: SecretReader,
    *,
    workspace_id: UUID,
    exchange: str,
) -> StreamConnectionCredentials:
    """Raises MarketDataAccessError (access_unavailable / credentials_missing
    / credentials_unreadable -- the same codes
    app.market_data.infrastructure.page_access.PageAccess.resolve uses) on
    any failure. Never returns partial or placeholder credentials."""
    if exchange not in _SUPPORTED_EXCHANGES:
        raise MarketDataAccessError(f"Unsupported exchange: {exchange}", "access_unavailable")
    row = _fetch_row(db, workspace_id, exchange)
    if row is None:
        raise MarketDataAccessError("Market-data access unavailable", "access_unavailable")
    connection, account = row
    return _credentials_from_row(connection, account, secrets, exchange)


def _fetch_row(
    db: Session, workspace_id: UUID, exchange: str
) -> tuple[ExchangeConnection, ExternalAccount] | None:
    row = db.execute(
        selected_account_statement(workspace_id).where(Exchange.code == exchange)
    ).one_or_none()
    if row is None:
        return None
    _exchange, connection, account, _selection = row
    return (connection, account)


def _credentials_from_row(
    connection: ExchangeConnection,
    account: ExternalAccount,
    secrets: SecretReader,
    exchange: str,
) -> StreamConnectionCredentials:
    ensure_sandbox_endpoint(exchange, connection)
    credentials = load_credentials(secrets, connection.secret_ref, exchange)
    if exchange == "oanda":
        try:
            account_id = secrets.decrypt_text(account.external_account_ref_encrypted)
        except ValueError as exc:
            raise MarketDataAccessError("Credentials unreadable", "credentials_unreadable") from exc
        return StreamConnectionCredentials(
            exchange="oanda",
            base_url=connection.api_base_url,
            token=credentials["token"],
            account_id=account_id,
        )
    return StreamConnectionCredentials(
        exchange="binance",
        base_url=connection.api_base_url,
        api_key=credentials["api_key"],
        secret_key=credentials["secret_key"],
    )
