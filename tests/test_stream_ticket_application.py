"""Application-layer tests for stream ticket issuance. See
docs/plans/realtime-market-data-stream.md (work unit 2)."""

import ast
import inspect
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from app.market_data.application import stream_tickets as ticket_application
from app.market_data.application.use_cases import MarketDataApplicationError
from app.market_data.infrastructure.leases import FeedKey
from app.market_data.infrastructure.page_access import AccessSnapshot
from app.services.market_data import MarketDataAccessError

SECRET = "test-signing-secret"


def test_application_has_no_http_or_response_schema_imports() -> None:
    tree = ast.parse(inspect.getsource(ticket_application))
    modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(
        module and module.startswith(("fastapi", "app.api", "app.schemas")) for module in modules
    )


def _access(**overrides) -> AccessSnapshot:
    kwargs = {
        "exchange": "oanda",
        "symbol": "USD_JPY",
        "base_url": "https://api-fxpractice.oanda.com",
        "connection_id": uuid4(),
        "account_id": uuid4(),
        "secret_ref": "local-encrypted://" + "0" * 32,
        "credentials_updated_at": None,
        "selection_updated_at": MagicMock(),
    }
    kwargs.update(overrides)
    return AccessSnapshot(**kwargs)


def test_issues_ticket_scoped_to_resolved_feed() -> None:
    db = MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    resolve_access = MagicMock(return_value=_access())

    result = ticket_application.issue_stream_ticket(
        db, workspace_id, instrument_id, "1m", resolve_access, SECRET, 60
    )

    resolve_access.assert_called_once_with(db, FeedKey(workspace_id, instrument_id, "1m"))
    assert result.exchange == "oanda"
    assert result.symbol == "USD_JPY"
    assert result.timeframe == "1m"
    claims = jwt.decode(result.ticket, SECRET, algorithms=["HS256"])
    assert claims["workspace_id"] == str(workspace_id)
    assert claims["exchange"] == "oanda"
    assert claims["symbol"] == "USD_JPY"


def test_rejects_unsupported_timeframe_before_resolving_access() -> None:
    resolve_access = MagicMock()

    with pytest.raises(MarketDataApplicationError) as exc_info:
        ticket_application.issue_stream_ticket(
            MagicMock(), uuid4(), uuid4(), "2m", resolve_access, SECRET, 60
        )

    assert exc_info.value.code == "invalid_input"
    resolve_access.assert_not_called()


@pytest.mark.parametrize(
    "access_error_code",
    ["access_unavailable", "credentials_missing", "credentials_unreadable"],
)
def test_translates_known_access_errors_without_leaking_message(access_error_code) -> None:
    resolve_access = MagicMock(
        side_effect=MarketDataAccessError("do-not-leak-this-detail", access_error_code)
    )

    with pytest.raises(MarketDataApplicationError) as exc_info:
        ticket_application.issue_stream_ticket(
            MagicMock(), uuid4(), uuid4(), "1m", resolve_access, SECRET, 60
        )

    assert exc_info.value.code == access_error_code


def test_unknown_access_error_code_falls_back_to_access_unavailable() -> None:
    resolve_access = MagicMock(
        side_effect=MarketDataAccessError("some other failure", "some_new_code")
    )

    with pytest.raises(MarketDataApplicationError) as exc_info:
        ticket_application.issue_stream_ticket(
            MagicMock(), uuid4(), uuid4(), "1m", resolve_access, SECRET, 60
        )

    assert exc_info.value.code == "access_unavailable"
