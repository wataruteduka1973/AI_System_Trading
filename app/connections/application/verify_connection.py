import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.exchanges.binance import (
    BinanceApiError,
    BinanceAuthenticationError,
    BinanceSpotTestnetClient,
    mask_api_key,
)
from app.exchanges.oanda import (
    OandaApiError,
    OandaAuthenticationError,
    OandaPracticeClient,
    mask_account_id,
)
from app.models.catalog import AuditLog, Exchange, ExchangeConnection, ExternalAccount
from app.services.secrets import LocalEncryptedSecretStore

VerificationErrorCode = Literal[
    "connection_not_found",
    "exchange_missing",
    "unsupported_environment",
    "credentials_missing",
    "credentials_unavailable",
    "credentials_invalid",
    "authentication_failed",
    "communication_failed",
    "result_persistence_failed",
]


class ConnectionVerificationError(RuntimeError):
    def __init__(self, code: VerificationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OandaAccountResult:
    account_ref_masked: str
    alias: str | None
    currency: str
    hedging_enabled: bool | None
    margin_rate: str | None
    gslo_mode: str | None
    usd_jpy_tradeable: bool


@dataclass(frozen=True)
class OandaVerificationResult:
    connection_id: UUID
    status: str
    accounts: list[OandaAccountResult]


@dataclass(frozen=True)
class BinanceAccountResult:
    account_ref_masked: str
    account_type: str
    permissions: list[str]
    can_trade: bool
    can_deposit: bool
    can_withdraw: bool
    nonzero_asset_count: int
    btc_jpy_tradeable: bool


@dataclass(frozen=True)
class BinanceVerificationResult:
    connection_id: UUID
    status: str
    accounts: list[BinanceAccountResult]


VerificationResult = OandaVerificationResult | BinanceVerificationResult


class VerifyConnectionUseCase:
    """Verify one paper-only exchange connection and persist its safe account metadata."""

    def __init__(
        self,
        db: Session,
        secret_store: LocalEncryptedSecretStore,
        oanda_client: OandaPracticeClient,
        binance_client: BinanceSpotTestnetClient,
    ) -> None:
        self.db = db
        self.secret_store = secret_store
        self.oanda_client = oanda_client
        self.binance_client = binance_client

    async def execute(self, workspace_id: UUID, connection_id: UUID) -> VerificationResult:
        connection = self.db.scalar(
            select(ExchangeConnection).where(
                ExchangeConnection.id == connection_id,
                ExchangeConnection.workspace_id == workspace_id,
            )
        )
        if connection is None:
            raise ConnectionVerificationError("connection_not_found", "Connection not found")
        exchange = self.db.get(Exchange, connection.exchange_id)
        if exchange is None:
            raise ConnectionVerificationError("exchange_missing", "Exchange is missing")
        supported_connection = (
            exchange.code == "oanda" and connection.environment == "practice"
        ) or (exchange.code == "binance" and connection.environment == "testnet")
        if not supported_connection:
            raise ConnectionVerificationError(
                "unsupported_environment",
                "Only OANDA practice and Binance Spot Testnet connections can be verified",
            )
        credentials = self._load_credentials(connection)
        if exchange.code == "binance":
            return await self._verify_binance(connection, credentials)
        return await self._verify_oanda(connection, credentials)

    def _load_credentials(self, connection: ExchangeConnection) -> dict[str, str]:
        if connection.secret_ref is None:
            raise ConnectionVerificationError(
                "credentials_missing", "Connection credentials are missing"
            )
        try:
            return self.secret_store.get(connection.secret_ref)
        except (KeyError, ValueError) as exc:
            raise ConnectionVerificationError(
                "credentials_unavailable", "Connection credentials cannot be loaded"
            ) from exc

    async def _verify_oanda(
        self, connection: ExchangeConnection, credentials: dict[str, str]
    ) -> OandaVerificationResult:
        token = credentials.get("token")
        if not token:
            raise ConnectionVerificationError(
                "credentials_invalid", "OANDA credentials must include a token"
            )
        connection.status = "verifying"
        self.db.commit()
        try:
            accounts = await self.oanda_client.verify_and_list_accounts(
                connection.api_base_url, token
            )
        except OandaAuthenticationError as exc:
            self._record_failure(connection, "authentication_failed")
            raise ConnectionVerificationError("authentication_failed", str(exc)) from exc
        except OandaApiError as exc:
            self._record_failure(connection, "communication_failed")
            raise ConnectionVerificationError("communication_failed", str(exc)) from exc
        except Exception as exc:
            self._record_failure(connection, "communication_failed")
            raise ConnectionVerificationError(
                "communication_failed", "OANDA verification could not be completed"
            ) from exc

        now = datetime.now(UTC)
        response_accounts: list[OandaAccountResult] = []
        for account in accounts:
            account_hash = hashlib.sha256(account.account_id.encode("utf-8")).hexdigest()
            external_account = self._find_external_account(connection.id, account_hash)
            if external_account is None:
                external_account = ExternalAccount(
                    connection_id=connection.id,
                    external_account_ref_hash=account_hash,
                    external_account_ref_encrypted=self.secret_store.encrypt_text(
                        account.account_id
                    ),
                    external_account_ref_masked=mask_account_id(account.account_id),
                    environment="practice",
                    currency=account.currency,
                )
                self.db.add(external_account)
            external_account.alias = account.alias
            external_account.hedging_enabled = account.hedging_enabled
            external_account.margin_rate = account.margin_rate
            external_account.gslo_mode = account.gslo_mode
            external_account.capabilities = {"usd_jpy_tradeable": account.usd_jpy_tradeable}
            external_account.status = "active"
            external_account.synced_at = now
            response_accounts.append(
                OandaAccountResult(
                    account_ref_masked=external_account.external_account_ref_masked,
                    alias=account.alias,
                    currency=account.currency,
                    hedging_enabled=account.hedging_enabled,
                    margin_rate=(
                        str(account.margin_rate) if account.margin_rate is not None else None
                    ),
                    gslo_mode=account.gslo_mode,
                    usd_jpy_tradeable=account.usd_jpy_tradeable,
                )
            )
        connection.status = "verified"
        connection.last_verified_at = now
        connection.verification_outcome = "success"
        connection.capabilities = {
            "account_count": len(accounts),
            "usd_jpy_tradeable": any(account.usd_jpy_tradeable for account in accounts),
            "read_only_verified": True,
        }
        self._add_verification_audit(connection, account_count=len(accounts))
        self._commit_result()
        return OandaVerificationResult(connection.id, connection.status, response_accounts)

    async def _verify_binance(
        self, connection: ExchangeConnection, credentials: dict[str, str]
    ) -> BinanceVerificationResult:
        api_key = credentials.get("api_key")
        secret_key = credentials.get("secret_key")
        if not api_key or not secret_key:
            raise ConnectionVerificationError(
                "credentials_invalid", "Binance credentials must include api_key and secret_key"
            )
        connection.status = "verifying"
        self.db.commit()
        try:
            account = await self.binance_client.verify_account(
                connection.api_base_url, api_key, secret_key
            )
        except BinanceAuthenticationError as exc:
            self._record_failure(connection, "authentication_failed")
            raise ConnectionVerificationError("authentication_failed", str(exc)) from exc
        except BinanceApiError as exc:
            self._record_failure(connection, "communication_failed")
            raise ConnectionVerificationError("communication_failed", str(exc)) from exc
        except Exception as exc:
            self._record_failure(connection, "communication_failed")
            raise ConnectionVerificationError(
                "communication_failed", "Binance verification could not be completed"
            ) from exc

        now = datetime.now(UTC)
        account_hash = hashlib.sha256(account.account_ref.encode("utf-8")).hexdigest()
        external_account = self._find_external_account(connection.id, account_hash)
        if external_account is None:
            external_account = ExternalAccount(
                connection_id=connection.id,
                external_account_ref_hash=account_hash,
                external_account_ref_encrypted=self.secret_store.encrypt_text(account.account_ref),
                external_account_ref_masked=mask_api_key(account.account_ref),
                environment="testnet",
                currency="MULTI",
            )
            self.db.add(external_account)
        external_account.alias = "Binance Spot Testnet"
        external_account.capabilities = {
            "account_type": account.account_type,
            "permissions": list(account.permissions),
            "can_trade": account.can_trade,
            "can_deposit": account.can_deposit,
            "can_withdraw": account.can_withdraw,
            "nonzero_asset_count": account.nonzero_asset_count,
            "btc_jpy_tradeable": account.btc_jpy_tradeable,
        }
        external_account.status = "active"
        external_account.synced_at = now
        connection.status = "verified"
        connection.last_verified_at = now
        connection.verification_outcome = "success"
        connection.capabilities = {
            **external_account.capabilities,
            "account_count": 1,
            "read_only_verified": True,
        }
        self._add_verification_audit(connection, account_count=1)
        self._commit_result()
        response_account = BinanceAccountResult(
            account_ref_masked=external_account.external_account_ref_masked,
            account_type=account.account_type,
            permissions=list(account.permissions),
            can_trade=account.can_trade,
            can_deposit=account.can_deposit,
            can_withdraw=account.can_withdraw,
            nonzero_asset_count=account.nonzero_asset_count,
            btc_jpy_tradeable=account.btc_jpy_tradeable,
        )
        return BinanceVerificationResult(connection.id, connection.status, [response_account])

    def _find_external_account(
        self, connection_id: UUID, account_hash: str
    ) -> ExternalAccount | None:
        return self.db.scalar(
            select(ExternalAccount).where(
                ExternalAccount.connection_id == connection_id,
                ExternalAccount.external_account_ref_hash == account_hash,
            )
        )

    def _record_failure(
        self,
        connection: ExchangeConnection,
        outcome: Literal["authentication_failed", "communication_failed"],
    ) -> None:
        connection.status = "invalid"
        connection.last_verified_at = datetime.now(UTC)
        connection.verification_outcome = outcome
        self._add_verification_audit(connection)
        self.db.commit()

    def _add_verification_audit(
        self, connection: ExchangeConnection, *, account_count: int | None = None
    ) -> None:
        after: dict[str, object] = {"verification_outcome": connection.verification_outcome}
        if account_count is not None:
            after["account_count"] = account_count
        self.db.add(
            AuditLog(
                workspace_id=connection.workspace_id,
                actor_id=None,
                action="connection.verification_completed",
                resource_type="exchange_connection",
                resource_id=connection.id,
                before_data=None,
                after_data=after,
                correlation_id=uuid4(),
                ip_address=None,
                user_agent=None,
            )
        )

    def _commit_result(self) -> None:
        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise ConnectionVerificationError(
                "result_persistence_failed",
                "Verification succeeded, but its result could not be saved",
            ) from exc
