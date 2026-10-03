"""Whether a workspace may read market data from an exchange right now, and with
which connection: its selected, active account on a verified connection of that
exchange, in the account's own environment, on a Practice/Testnet endpoint.

The one place these conditions live (docs/plans/market-data-services-consolidation.md
Unit 3). The Worker (`PageAccess.resolve`), the realtime stream
(`stream_connection_access`) and the API's pre-check before enqueuing work
(`check_collection_access`) all resolve access through `selected_account_statement`,
so the API cannot accept work the Worker would then refuse. The statement takes a
shared lock so the decision holds until the caller's transaction commits."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.exchanges.binance import BinanceApiError, BinanceSpotTestnetClient
from app.exchanges.oanda import OandaApiError, OandaPracticeClient
from app.models.connections import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    WorkspaceAccountSelection,
)
from app.models.instruments import Instrument
from app.models.workspace import Workspace

ACCESS_ERROR_CODES = frozenset(
    {"access_unavailable", "credentials_missing", "credentials_unreadable"}
)


class MarketDataAccessError(RuntimeError):
    def __init__(self, message: str, code: str = "configuration_error") -> None:
        super().__init__(message)
        self.code = code


class StoredSecrets(Protocol):
    """What this module reads from `app.security.secret_store.LocalEncryptedSecretStore`."""

    def get(self, secret_ref: str) -> dict[str, str]: ...


def _restrict_to_selected_account[T: tuple[object, ...]](
    statement: Select[T], workspace_id: UUID
) -> Select[T]:
    """Joins `statement` (whose FROM already has `Exchange`) to the workspace's selected,
    active account on a verified connection of that exchange in the account's own
    environment. Every access decision in this module goes through these conditions."""
    return (
        statement.join(
            WorkspaceAccountSelection,
            (WorkspaceAccountSelection.workspace_id == workspace_id)
            & (WorkspaceAccountSelection.exchange_id == Exchange.id),
        )
        .join(Workspace, Workspace.id == WorkspaceAccountSelection.workspace_id)
        .join(ExternalAccount, ExternalAccount.id == WorkspaceAccountSelection.external_account_id)
        .join(ExchangeConnection, ExchangeConnection.id == ExternalAccount.connection_id)
        .where(
            Exchange.status == "active",
            Workspace.status == "active",
            ExternalAccount.status == "active",
            ExchangeConnection.workspace_id == workspace_id,
            ExchangeConnection.exchange_id == Exchange.id,
            ExchangeConnection.status == "verified",
            ExternalAccount.environment == ExchangeConnection.environment,
        )
    )


def selected_account_statement(
    workspace_id: UUID,
) -> Select[tuple[Exchange, ExchangeConnection, ExternalAccount, WorkspaceAccountSelection]]:
    """Rows of (exchange, connection, account, selection) the workspace may use, under a
    shared lock that holds the decision until the caller commits; callers narrow it to
    one exchange or one instrument."""
    return _restrict_to_selected_account(
        select(Exchange, ExchangeConnection, ExternalAccount, WorkspaceAccountSelection),
        workspace_id,
    ).with_for_update(read=True)


def instrument_is_readable(db: Session, workspace_id: UUID, instrument_id: UUID) -> bool:
    """Whether the workspace may read stored market data for `instrument_id`: the same
    account conditions as every other decision here, without the lock (nothing is
    written on the strength of it) and without the endpoint or credential checks
    (reading stored candles never contacts the exchange)."""
    statement = _restrict_to_selected_account(
        select(Instrument.id).join(Exchange, Instrument.exchange_id == Exchange.id),
        workspace_id,
    ).where(Instrument.id == instrument_id, Instrument.status == "active")
    return db.scalar(statement) is not None


def ensure_sandbox_endpoint(exchange_code: str, connection: ExchangeConnection) -> None:
    """Only OANDA Practice and Binance Spot Testnet endpoints are ever used."""
    if exchange_code == "binance" and connection.environment == "testnet":
        BinanceSpotTestnetClient._validate_testnet_url(connection.api_base_url)
    elif exchange_code == "oanda" and connection.environment == "practice":
        OandaPracticeClient._validate_practice_url(connection.api_base_url)
    else:
        raise MarketDataAccessError("Unsupported environment", "access_unavailable")


def load_credentials(
    secrets: StoredSecrets, secret_ref: str | None, exchange_code: str
) -> dict[str, str]:
    """The stored credentials, with the keys `exchange_code` needs present."""
    if not secret_ref:
        raise MarketDataAccessError("Credentials missing", "credentials_missing")
    try:
        credentials = secrets.get(secret_ref)
    except (KeyError, ValueError, OSError) as exc:
        raise MarketDataAccessError("Credentials unreadable", "credentials_unreadable") from exc
    required = ("token",) if exchange_code == "oanda" else ("api_key", "secret_key")
    if not all(credentials.get(key) for key in required):
        raise MarketDataAccessError("Credentials missing", "credentials_missing")
    return credentials


@dataclass(frozen=True)
class InstrumentAccess:
    instrument: Instrument
    exchange: Exchange
    connection: ExchangeConnection
    account: ExternalAccount
    selection: WorkspaceAccountSelection
    secret_ref: str = field(repr=False)


def resolve_instrument_access(
    db: Session, workspace_id: UUID, instrument_id: UUID
) -> InstrumentAccess:
    """The connection the workspace reads `instrument_id` through, or
    `MarketDataAccessError`. Does not read the stored credentials."""
    row = db.execute(
        selected_account_statement(workspace_id)
        .add_columns(Instrument)
        .join(Instrument, Instrument.exchange_id == Exchange.id)
        .where(Instrument.id == instrument_id, Instrument.status == "active")
    ).one_or_none()
    if row is None:
        raise MarketDataAccessError("Market-data access unavailable", "access_unavailable")
    exchange, connection, account, selection, instrument = row
    ensure_sandbox_endpoint(exchange.code, connection)
    if not connection.secret_ref:
        raise MarketDataAccessError("Credentials missing", "credentials_missing")
    return InstrumentAccess(
        instrument, exchange, connection, account, selection, connection.secret_ref
    )


def check_collection_access(
    db: Session, secrets: StoredSecrets, workspace_id: UUID, instrument_id: UUID
) -> None:
    """The API's check before it enqueues a backfill or enables collection: the same
    conditions the Worker applies, and readable, complete credentials. Sends no
    exchange request.

    A non-Practice/Testnet endpoint is reported as `access_unavailable` here: the
    Worker classifies the exchange client's own error, but the API needs an access
    error it can answer with 409 instead of failing the request."""
    try:
        access = resolve_instrument_access(db, workspace_id, instrument_id)
    except (BinanceApiError, OandaApiError) as exc:
        raise MarketDataAccessError("Unsupported endpoint", "access_unavailable") from exc
    load_credentials(secrets, access.secret_ref, access.exchange.code)
