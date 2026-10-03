from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.connections import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    WorkspaceAccountSelection,
)
from app.models.instruments import Instrument
from app.services.secrets import LocalEncryptedSecretStore


class MarketDataAccessError(RuntimeError):
    def __init__(self, message: str, code: str = "configuration_error") -> None:
        super().__init__(message)
        self.code = code


class CandleIngestionService:
    def __init__(
        self,
        db: Session,
        secret_store: LocalEncryptedSecretStore,
    ) -> None:
        self.db = db
        self.secret_store = secret_store

    def validate_configuration(self, workspace_id: UUID, instrument_id: UUID) -> None:
        """Check local access and decryptability without sending any exchange request."""
        _instrument, exchange, connection = self._resolve_access(workspace_id, instrument_id)
        credentials = self._load_credentials(connection)
        required = ("token",) if exchange.code == "oanda" else ("api_key", "secret_key")
        if not all(credentials.get(key) for key in required):
            raise MarketDataAccessError(
                "Exchange credentials are incomplete", "credentials_missing"
            )

    def _resolve_access(
        self, workspace_id: UUID, instrument_id: UUID
    ) -> tuple[Instrument, Exchange, ExchangeConnection]:
        row = self.db.execute(
            select(Instrument, Exchange, ExchangeConnection)
            .join(Exchange, Instrument.exchange_id == Exchange.id)
            .join(
                WorkspaceAccountSelection,
                (WorkspaceAccountSelection.workspace_id == workspace_id)
                & (WorkspaceAccountSelection.exchange_id == Exchange.id),
            )
            .join(
                ExternalAccount,
                ExternalAccount.id == WorkspaceAccountSelection.external_account_id,
            )
            .join(ExchangeConnection, ExternalAccount.connection_id == ExchangeConnection.id)
            .where(
                Instrument.id == instrument_id,
                ExchangeConnection.workspace_id == workspace_id,
                ExchangeConnection.exchange_id == Exchange.id,
                ExchangeConnection.status == "verified",
                ExternalAccount.status == "active",
            )
        ).one_or_none()
        if row is None:
            raise MarketDataAccessError(
                "Instrument requires a selected active account from a verified connection"
            )
        instrument, exchange, connection = row
        return instrument, exchange, connection

    def _load_credentials(self, connection: ExchangeConnection) -> dict[str, str]:
        if not connection.secret_ref:
            raise MarketDataAccessError(
                "Selected connection credentials are missing", "credentials_missing"
            )
        try:
            return self.secret_store.get(connection.secret_ref)
        except (KeyError, ValueError, OSError) as exc:
            raise MarketDataAccessError(
                "Selected connection credentials cannot be loaded", "credentials_unreadable"
            ) from exc
