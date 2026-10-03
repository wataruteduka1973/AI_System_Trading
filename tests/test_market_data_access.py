"""`app/market_data/infrastructure/access.py` (docs/plans/market-data-services-consolidation.md
Unit 3): the one place that decides whether a workspace may read market data through a
connection. Before it existed, the API's pre-check skipped conditions the Worker applied,
so the API could accept a backfill the Worker would then refuse."""

import ast
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.market_data.infrastructure import access
from app.models.connections import (
    Exchange,
    ExchangeConnection,
    ExternalAccount,
    WorkspaceAccountSelection,
)
from app.models.instruments import Instrument
from sqlalchemy.dialects import postgresql

APP = Path(__file__).resolve().parents[1] / "app"


def _row(
    *,
    exchange_code: str = "binance",
    environment: str = "testnet",
    base_url: str = "https://testnet.binance.vision",
    secret_ref: str | None = "local-encrypted://" + "0" * 32,
) -> tuple:
    exchange = Exchange(id=uuid4(), code=exchange_code, status="active")
    connection = ExchangeConnection(
        id=uuid4(), environment=environment, api_base_url=base_url, secret_ref=secret_ref
    )
    account = ExternalAccount(id=uuid4(), environment=environment, status="active")
    selection = WorkspaceAccountSelection(workspace_id=uuid4(), exchange_id=exchange.id)
    instrument = Instrument(id=uuid4(), symbol="BTCUSDT", status="active")
    return exchange, connection, account, selection, instrument


def _db_returning(row: tuple | None) -> MagicMock:
    db = MagicMock()
    db.execute.return_value.one_or_none.return_value = row
    return db


class _Secrets:
    def __init__(self, values: dict[str, str] | Exception) -> None:
        self.values = values

    def get(self, secret_ref: str) -> dict[str, str]:
        if isinstance(self.values, Exception):
            raise self.values
        return self.values


def test_the_instrument_query_carries_every_condition_the_worker_applies() -> None:
    db = _db_returning(_row())
    access.resolve_instrument_access(db, uuid4(), uuid4())

    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    for condition in (
        "instrument.id =",
        "instrument.status =",
        "exchange.status =",
        "workspace.status =",
        "external_account.status =",
        "exchange_connection.status =",
        "exchange_connection.workspace_id =",
        "exchange_connection.exchange_id = fx.exchange.id",
        "external_account.environment = fx.exchange_connection.environment",
    ):
        assert condition in sql, condition
    assert sql.rstrip().endswith("FOR SHARE")


def test_no_matching_row_is_access_unavailable() -> None:
    with pytest.raises(access.MarketDataAccessError) as raised:
        access.check_collection_access(_db_returning(None), _Secrets({}), uuid4(), uuid4())
    assert raised.value.code == "access_unavailable"


@pytest.mark.parametrize(
    ("exchange_code", "environment", "base_url"),
    [
        ("binance", "testnet", "https://api.binance.com"),
        ("oanda", "practice", "https://api-fxtrade.oanda.com"),
    ],
)
def test_a_non_sandbox_endpoint_is_access_unavailable_before_credentials_are_read(
    exchange_code: str, environment: str, base_url: str
) -> None:
    # Regression: the exchange client's own error used to escape the API pre-check.
    secrets = MagicMock()
    db = _db_returning(
        _row(exchange_code=exchange_code, environment=environment, base_url=base_url)
    )

    with pytest.raises(access.MarketDataAccessError) as raised:
        access.check_collection_access(db, secrets, uuid4(), uuid4())
    assert raised.value.code == "access_unavailable"
    secrets.get.assert_not_called()


def test_an_environment_that_does_not_match_the_exchange_is_access_unavailable() -> None:
    db = _db_returning(_row(environment="live"))

    with pytest.raises(access.MarketDataAccessError) as raised:
        access.check_collection_access(db, _Secrets({}), uuid4(), uuid4())
    assert raised.value.code == "access_unavailable"


@pytest.mark.parametrize(
    ("secret_ref", "stored", "code"),
    [
        (None, {}, "credentials_missing"),
        ("ref", ValueError("bad key"), "credentials_unreadable"),
        ("ref", {"api_key": "k"}, "credentials_missing"),
    ],
)
def test_credentials_must_be_present_readable_and_complete(
    secret_ref: str | None, stored: dict[str, str] | Exception, code: str
) -> None:
    db = _db_returning(_row(secret_ref=secret_ref))

    with pytest.raises(access.MarketDataAccessError) as raised:
        access.check_collection_access(db, _Secrets(stored), uuid4(), uuid4())
    assert raised.value.code == code
    assert "bad key" not in str(raised.value)


def test_complete_credentials_on_a_sandbox_connection_pass() -> None:
    db = _db_returning(_row())
    access.check_collection_access(
        db, _Secrets({"api_key": "k", "secret_key": "s"}), uuid4(), uuid4()
    )


def test_market_data_infrastructure_resolves_account_selection_only_in_access_module() -> None:
    # The three copies of this query drifted apart before; keep it in one place.
    infrastructure = APP / "market_data" / "infrastructure"
    referencing = sorted(
        path.name
        for path in infrastructure.glob("*.py")
        if any(
            isinstance(node, ast.Name) and node.id == "WorkspaceAccountSelection"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    )
    assert referencing == ["access.py"]
