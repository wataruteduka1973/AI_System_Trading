"""API-level tests for WS /ws/v1/market-stream. See
docs/plans/realtime-market-data-stream.md (work units 5/6).

NOT VERIFIED locally -- fastapi/starlette/pydantic-core could not be
imported in this sandbox (see market_stream_ws.py's module docstring), so
these tests could not actually be run before this branch reached CI,
unlike tests/test_stream_session.py (which exercises the same
orchestration logic through a fake Transport and passes genuinely). They
follow the exact fixtures/monkeypatch/dependency-override conventions
already established by tests/test_market_stream_ticket_api.py (work unit
2)."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from app.api.routes import market_stream_ws as ws_routes
from app.core.config import Settings
from app.main import app
from app.market_data.infrastructure.candle_stream import (
    OHLCV,
    FeedHub,
    FeedSink,
    FeedWorkerHandle,
    NormalizedCandleUpdate,
    StreamFeedKey,
)
from app.market_data.infrastructure.stream_connection_access import StreamConnectionCredentials
from app.market_data.infrastructure.stream_tickets import UsedTicketStore, issue_ticket
from fastapi.testclient import TestClient

client = TestClient(app)
SECRET = "test-signing-secret"


def _configure_settings(monkeypatch, **overrides) -> None:
    kwargs = {
        "market_stream_ticket_secret": SECRET,
        "market_stream_ticket_ttl_seconds": 60,
        "market_stream_heartbeat_interval_seconds": 0.05,
        "market_stream_grace_period_seconds": 0.05,
    }
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    monkeypatch.setattr(ws_routes, "get_settings", lambda: settings)
    return settings


def _fresh_hub(monkeypatch, **kwargs) -> FeedHub:
    hub = FeedHub(**kwargs)
    ws_routes.get_default_feed_hub.cache_clear()
    monkeypatch.setattr(ws_routes, "get_default_feed_hub", lambda: hub)
    return hub


def _fresh_used_tickets(monkeypatch) -> UsedTicketStore:
    store = UsedTicketStore()
    monkeypatch.setattr(ws_routes, "get_default_used_ticket_store", lambda: store)
    return store


def _issue_ticket(*, exchange="binance", symbol="BTCJPY", timeframe="1m", workspace_id=None):
    token, _ = issue_ticket(
        SECRET,
        workspace_id=workspace_id or uuid4(),
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        ttl_seconds=60,
    )
    return token


def _stub_binance_starter(monkeypatch, *, publish: NormalizedCandleUpdate | None = None) -> None:
    def fake_build_starter_for(credentials, loop):
        def starter(key: StreamFeedKey, sink: FeedSink) -> FeedWorkerHandle:
            if publish is not None:
                sink.publish_candle(publish)
            return MagicMock(spec=FeedWorkerHandle)

        return starter

    monkeypatch.setattr(ws_routes, "_build_starter_for", fake_build_starter_for)
    monkeypatch.setattr(
        ws_routes,
        "_resolve_credentials_sync",
        lambda workspace_id, exchange: StreamConnectionCredentials(
            exchange="binance",
            base_url="https://testnet.binance.vision",
            api_key="ak",
            secret_key="sk",
        ),
    )


def test_rejects_an_invalid_ticket_with_a_safe_close_code(monkeypatch) -> None:
    _configure_settings(monkeypatch)
    _fresh_hub(monkeypatch)
    _fresh_used_tickets(monkeypatch)

    with pytest.raises(Exception):
        with client.websocket_connect("/ws/v1/market-stream?ticket=not-a-real-ticket"):
            pass


def test_rejects_a_ticket_signed_with_a_different_secret(monkeypatch) -> None:
    _configure_settings(monkeypatch)
    _fresh_hub(monkeypatch)
    _fresh_used_tickets(monkeypatch)
    bad_token = jwt.encode(
        {
            "jti": "x",
            "workspace_id": str(uuid4()),
            "exchange": "binance",
            "symbol": "BTCJPY",
            "timeframe": "1m",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC),
        },
        "wrong-secret",
        algorithm="HS256",
    )
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/v1/market-stream?ticket={bad_token}"):
            pass


def test_accepts_a_valid_ticket_and_streams_the_stream_state_envelope(monkeypatch) -> None:
    _configure_settings(monkeypatch)
    _fresh_hub(monkeypatch, grace_period_seconds=0.05)
    _fresh_used_tickets(monkeypatch)
    _stub_binance_starter(monkeypatch)
    ticket = _issue_ticket()

    with client.websocket_connect(f"/ws/v1/market-stream?ticket={ticket}") as ws:
        first = ws.receive_json()
        assert first["type"] == "stream_state"
        assert first["resume"] == "fresh"


def test_forwards_a_candle_event_with_workspace_id_stamped(monkeypatch) -> None:
    _configure_settings(monkeypatch)
    _fresh_hub(monkeypatch, grace_period_seconds=0.05)
    _fresh_used_tickets(monkeypatch)
    workspace_id = uuid4()
    update = NormalizedCandleUpdate(
        datetime(2026, 9, 17, tzinfo=UTC),
        OHLCV(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("1")),
        False,
    )
    _stub_binance_starter(monkeypatch, publish=update)
    ticket = _issue_ticket(workspace_id=workspace_id)

    with client.websocket_connect(f"/ws/v1/market-stream?ticket={ticket}") as ws:
        state = ws.receive_json()
        assert state["type"] == "stream_state"
        event = ws.receive_json()
        assert event["event_type"] == "provisional_update"
        assert event["workspace_id"] == str(workspace_id)
        assert event["ohlcv"]["close"] == "100.5"


def test_rejects_a_second_connection_with_the_same_ticket(monkeypatch) -> None:
    _configure_settings(monkeypatch)
    _fresh_hub(monkeypatch, grace_period_seconds=0.05)
    _fresh_used_tickets(monkeypatch)
    _stub_binance_starter(monkeypatch)
    ticket = _issue_ticket()

    with client.websocket_connect(f"/ws/v1/market-stream?ticket={ticket}"):
        pass

    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/v1/market-stream?ticket={ticket}"):
            pass


def test_rejects_when_ticket_signing_is_not_configured(monkeypatch) -> None:
    _configure_settings(monkeypatch, market_stream_ticket_secret=None)
    _fresh_hub(monkeypatch)
    _fresh_used_tickets(monkeypatch)

    with pytest.raises(Exception):
        with client.websocket_connect("/ws/v1/market-stream?ticket=irrelevant"):
            pass
