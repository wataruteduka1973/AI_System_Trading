"""Tests for app.market_data.infrastructure.stream_connection_access. See
docs/plans/realtime-market-data-stream.md (work unit 5)."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.market_data.infrastructure.stream_connection_access import (
    resolve_stream_connection_credentials,
)
from app.models.connections import ExchangeConnection, ExternalAccount
from app.services.market_data import MarketDataAccessError

WORKSPACE_ID = uuid4()


class _FakeSecrets:
    def __init__(self, values: dict, *, account_ref: str = "12-3456789") -> None:
        self._values = values
        self._account_ref = account_ref

    def get(self, secret_ref: str) -> dict:
        return self._values

    def decrypt_text(self, value: str) -> str:
        assert value == "encrypted-ref"
        return self._account_ref


def _session_returning(row) -> MagicMock:
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.one_or_none.return_value = row
    session.execute.return_value = execute_result
    return session


def _oanda_connection() -> tuple[ExchangeConnection, ExternalAccount]:
    connection = ExchangeConnection(
        workspace_id=WORKSPACE_ID,
        exchange_id=uuid4(),
        label="practice",
        environment="practice",
        api_base_url="https://api-fxpractice.oanda.com",
        secret_ref="local-encrypted://" + "0" * 32,
        status="verified",
    )
    account = ExternalAccount(
        connection_id=uuid4(),
        external_account_ref_encrypted="encrypted-ref",
        external_account_ref_hash="hash",
        external_account_ref_masked="***789",
        environment="practice",
        currency="USD",
        status="active",
    )
    return connection, account


def _binance_connection() -> tuple[ExchangeConnection, ExternalAccount]:
    connection = ExchangeConnection(
        workspace_id=WORKSPACE_ID,
        exchange_id=uuid4(),
        label="testnet",
        environment="testnet",
        api_base_url="https://testnet.binance.vision",
        secret_ref="local-encrypted://" + "1" * 32,
        status="verified",
    )
    account = ExternalAccount(
        connection_id=uuid4(),
        external_account_ref_encrypted="encrypted-ref",
        external_account_ref_hash="hash",
        external_account_ref_masked="***key",
        environment="testnet",
        currency="USDT",
        status="active",
    )
    return connection, account


def test_resolves_oanda_credentials() -> None:
    connection, account = _oanda_connection()
    db = _session_returning((connection, account))
    secrets = _FakeSecrets({"token": "the-token"}, account_ref="101-002")

    credentials = resolve_stream_connection_credentials(
        db, secrets, workspace_id=WORKSPACE_ID, exchange="oanda"
    )

    assert credentials.exchange == "oanda"
    assert credentials.base_url == "https://api-fxpractice.oanda.com"
    assert credentials.token == "the-token"
    assert credentials.account_id == "101-002"
    assert credentials.api_key is None


def test_resolves_binance_credentials() -> None:
    connection, account = _binance_connection()
    db = _session_returning((connection, account))
    secrets = _FakeSecrets({"api_key": "ak", "secret_key": "sk"})

    credentials = resolve_stream_connection_credentials(
        db, secrets, workspace_id=WORKSPACE_ID, exchange="binance"
    )

    assert credentials.exchange == "binance"
    assert credentials.base_url == "https://testnet.binance.vision"
    assert credentials.api_key == "ak"
    assert credentials.secret_key == "sk"
    assert credentials.token is None


def test_rejects_unsupported_exchange() -> None:
    db = _session_returning(None)
    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _FakeSecrets({}), workspace_id=WORKSPACE_ID, exchange="coinbase"
        )
    assert excinfo.value.code == "access_unavailable"
    db.execute.assert_not_called()  # rejected before ever querying


def test_raises_access_unavailable_when_no_row_matches() -> None:
    db = _session_returning(None)
    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _FakeSecrets({}), workspace_id=WORKSPACE_ID, exchange="oanda"
        )
    assert excinfo.value.code == "access_unavailable"


def test_rejects_environment_mismatch() -> None:
    connection, account = _oanda_connection()
    connection.environment = "live"  # never valid for this paper/testnet-only system
    db = _session_returning((connection, account))
    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _FakeSecrets({"token": "x"}), workspace_id=WORKSPACE_ID, exchange="oanda"
        )
    assert excinfo.value.code == "access_unavailable"


def test_rejects_non_practice_base_url_even_if_environment_says_practice() -> None:
    connection, account = _oanda_connection()
    connection.api_base_url = "https://api-fxtrade.oanda.com"  # live host, not practice
    db = _session_returning((connection, account))
    with pytest.raises(Exception):  # noqa: B017 -- OandaApiError from the defense-in-depth URL check
        resolve_stream_connection_credentials(
            db, _FakeSecrets({"token": "x"}), workspace_id=WORKSPACE_ID, exchange="oanda"
        )


def test_raises_credentials_missing_when_secret_ref_absent() -> None:
    connection, account = _oanda_connection()
    connection.secret_ref = None
    db = _session_returning((connection, account))
    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _FakeSecrets({"token": "x"}), workspace_id=WORKSPACE_ID, exchange="oanda"
        )
    assert excinfo.value.code == "credentials_missing"


def test_raises_credentials_missing_when_token_field_absent() -> None:
    connection, account = _oanda_connection()
    db = _session_returning((connection, account))
    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _FakeSecrets({}), workspace_id=WORKSPACE_ID, exchange="oanda"
        )
    assert excinfo.value.code == "credentials_missing"


def test_raises_credentials_missing_when_binance_keys_incomplete() -> None:
    connection, account = _binance_connection()
    db = _session_returning((connection, account))
    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _FakeSecrets({"api_key": "ak"}), workspace_id=WORKSPACE_ID, exchange="binance"
        )
    assert excinfo.value.code == "credentials_missing"


def test_raises_credentials_unreadable_when_secret_store_raises() -> None:
    connection, account = _oanda_connection()
    db = _session_returning((connection, account))

    class _BrokenSecrets:
        def get(self, secret_ref: str) -> dict:
            raise KeyError("gone")

        def decrypt_text(self, value: str) -> str:
            raise AssertionError("should not reach decryption")

    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _BrokenSecrets(), workspace_id=WORKSPACE_ID, exchange="oanda"
        )
    assert excinfo.value.code == "credentials_unreadable"


def test_raises_credentials_unreadable_when_account_ref_undecryptable() -> None:
    connection, account = _oanda_connection()
    db = _session_returning((connection, account))

    class _UndecryptableSecrets:
        def get(self, secret_ref: str) -> dict:
            return {"token": "x"}

        def decrypt_text(self, value: str) -> str:
            raise ValueError("cannot decrypt")

    with pytest.raises(MarketDataAccessError) as excinfo:
        resolve_stream_connection_credentials(
            db, _UndecryptableSecrets(), workspace_id=WORKSPACE_ID, exchange="oanda"
        )
    assert excinfo.value.code == "credentials_unreadable"
