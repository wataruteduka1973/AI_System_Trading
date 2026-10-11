"""The trade history API (app/api/routes/trade_history.py): the HTTP layer against stubbed
queries; the queries themselves are checked on a real PostgreSQL in
tests/test_trade_history_postgres.py."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.db.session import get_db
from app.main import app
from app.models.trading import Fill, TradeOrder, TradingPosition
from app.models.workspace import AppUser
from app.security.rbac import require_viewer_role
from app.trading.application import trade_history
from fastapi.testclient import TestClient

client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")
WORKSPACE = uuid4()
EXECUTED = datetime(2026, 10, 7, 12, 32, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    yield
    app.dependency_overrides.clear()


def _row(**overrides):
    order = TradeOrder(
        id=uuid4(),
        side="sell",
        order_type="market",
        status="filled",
        client_order_id="stop-abc",
    )
    fill = Fill(
        id=uuid4(),
        order_id=order.id,
        price=Decimal("780.37"),
        quantity=Decimal("20.953"),
        fee_amount=Decimal("16.35"),
        executed_at=EXECUTED,
    )
    values = dict(
        fill=fill,
        order=order,
        symbol="BNBUSDT",
        quote_asset="USDT",
        bot_id=uuid4(),
        bot_name="bnbusdt-4h-donchian",
        realized_pnl=Decimal("-315.58"),
        reason="stop_loss",
    )
    values.update(overrides)
    return trade_history.FillRow(**values)


def test_lists_fills_with_the_derived_figures_and_the_totals(monkeypatch) -> None:
    row = _row()
    seen = {}

    def fake(db, workspace_id, filters, *, limit, before=None):
        seen.update(filters=filters, limit=limit, before=before)
        return trade_history.FillPage(
            items=[row],
            next_before=(EXECUTED, row.fill.id),
            totals=[trade_history.FillTotals("USDT", 2, Decimal("33.02"), Decimal("-315.58"))],
        )

    monkeypatch.setattr(trade_history, "list_fills", fake)
    bot = uuid4()

    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/fills",
        params={
            "bot_id": str(bot),
            "side": "sell",
            "limit": 20,
            "from_time": "2026-10-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()
    (item,) = body["items"]
    assert (item["symbol"], item["side"], item["reason"]) == ("BNBUSDT", "sell", "stop_loss")
    assert Decimal(item["notional"]) == Decimal("780.37") * Decimal("20.953")
    assert Decimal(item["realized_pnl"]) == Decimal("-315.58")
    assert item["bot_name"] == "bnbusdt-4h-donchian"
    assert body["totals"][0]["quote_asset"] == "USDT" and body["totals"][0]["fills"] == 2
    assert body["next_before_id"] == str(row.fill.id)
    assert seen["filters"].bot_id == bot and seen["filters"].side == "sell" and seen["limit"] == 20


def test_the_last_page_has_no_cursor(monkeypatch) -> None:
    monkeypatch.setattr(
        trade_history,
        "list_fills",
        lambda db, workspace_id, filters, *, limit, before=None: trade_history.FillPage(
            items=[], next_before=None, totals=[]
        ),
    )

    body = client.get(f"/api/v1/workspaces/{WORKSPACE}/fills").json()

    assert body == {"items": [], "totals": [], "next_before": None, "next_before_id": None}


@pytest.mark.parametrize(
    "params",
    [
        {"before": "2026-10-07T00:00:00Z"},
        {"before_id": str(uuid4())},
        {"from_time": "2026-10-08T00:00:00Z", "to_time": "2026-10-07T00:00:00Z"},
        {"side": "hold"},
        {"limit": 0},
        {"limit": 1000},
        {"bot_id": "nope"},
    ],
)
def test_rejects_requests_that_make_no_sense(params) -> None:
    assert client.get(f"/api/v1/workspaces/{WORKSPACE}/fills", params=params).status_code == 422


def test_lists_positions_with_their_bot_and_result(monkeypatch) -> None:
    position = TradingPosition(
        id=uuid4(),
        side="long",
        status="closed",
        quantity=Decimal(0),
        average_entry_price=Decimal("795.43"),
        realized_pnl=Decimal("-315.58"),
        stop_price=None,
        opened_at=datetime(2026, 10, 5, 1, tzinfo=UTC),
        closed_at=EXECUTED,
    )
    seen = {}

    def fake(db, workspace_id, **kwargs):
        seen.update(kwargs)
        return 1, [
            trade_history.PositionRow(position, "BNBUSDT", "USDT", uuid4(), "bnbusdt-4h-donchian")
        ]

    monkeypatch.setattr(trade_history, "list_positions", fake)

    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/position-history",
        params={"status": ["closed"], "limit": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    (item,) = body["items"]
    assert (item["symbol"], item["status"], item["side"]) == ("BNBUSDT", "closed", "long")
    assert Decimal(item["realized_pnl"]) == Decimal("-315.58") and item["quantity"] in (
        "0",
        "0E-18",
    )
    assert seen["statuses"] == ["closed"] and seen["limit"] == 10


def test_a_position_status_other_than_open_or_closed_is_rejected() -> None:
    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/position-history", params={"status": "pending"}
    )

    assert response.status_code == 422
