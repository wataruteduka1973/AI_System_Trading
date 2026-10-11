"""The decision history's rules and API (app/trading/application/decision_history.py,
app/api/routes/decision_history.py): how a signal's outcome is derived, which risk checks count as
failed, and the HTTP layer against a stubbed query. The queries are checked on a real PostgreSQL in
tests/test_decision_history_postgres.py."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.db.session import get_db
from app.main import app
from app.models.strategy import Signal, TradingBot, TradingHalt
from app.models.workspace import AppUser
from app.security.rbac import require_viewer_role
from app.trading.application import decision_history
from app.trading.application.decision_history import derive_outcome, failed_checks
from fastapi.testclient import TestClient

client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")
WORKSPACE = uuid4()


@pytest.mark.parametrize(
    ("action", "has_intent", "closed", "risk", "expected"),
    [
        ("hold", False, False, None, "hold"),
        ("hold", False, False, "deny", "hold"),  # a hold never reaches the gate
        ("buy", True, False, "allow", "ordered"),
        ("buy", True, False, "allow_with_adjustment", "ordered"),
        ("sell", False, True, None, "closed"),
        ("sell", False, False, "deny", "denied"),
        ("buy", False, False, "allow", "halted"),
        ("buy", False, False, "allow_with_adjustment", "halted"),
        ("buy", False, False, None, "paused"),
        ("sell", True, True, "allow", "ordered"),  # an intent means it was placed
    ],
)
def test_what_became_of_a_signal(action, has_intent, closed, risk, expected) -> None:
    assert (
        derive_outcome(action, has_intent=has_intent, closed_position=closed, risk_outcome=risk)
        == expected
    )


def test_only_the_checks_that_did_not_pass_are_reported_with_their_figures() -> None:
    results = {
        "equity": {"passed": True, "value": "100"},
        "binance_no_short": {"passed": False},
        "per_trade_loss": {"passed": False, "pct": "0.02"},
        "daily_loss": {"passed": True, "note": "no snapshot yet in this window"},
        "quantity_calculation": {"passed": False, "final_quantity": "0"},
        "odd": "not a check",
    }

    failed = failed_checks(results)

    assert {check.name: check.detail for check in failed} == {
        "binance_no_short": {},
        "per_trade_loss": {"pct": "0.02"},
        "quantity_calculation": {"final_quantity": "0"},
    }
    assert failed_checks({}) == []


@pytest.fixture(autouse=True)
def _auth():
    session = MagicMock()
    session.get.return_value = TradingBot(id=uuid4(), workspace_id=WORKSPACE, name="b")
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    yield session
    app.dependency_overrides.clear()


def _entry(**overrides):
    signal = Signal(
        id=uuid4(),
        action="sell",
        rationale={"kind": "donchian_breakout", "close": "82757"},
        created_at=datetime(2026, 10, 8, 4, 0, tzinfo=UTC),
    )
    values = dict(
        signal=signal,
        bar_open_time=datetime(2026, 10, 8, 0, 0, tzinfo=UTC),
        bar_close_time=datetime(2026, 10, 8, 4, 0, tzinfo=UTC),
        outcome="denied",
        risk_outcome="deny",
        risk_reason_code="hard_limit_breach",
        failed_checks=[decision_history.FailedCheck("binance_no_short", {})],
        approved_quantity=None,
    )
    values.update(overrides)
    return decision_history.DecisionEntry(**values)


def test_the_history_carries_each_decision_the_summary_and_the_halts(monkeypatch) -> None:
    entry = _entry()
    halt = TradingHalt(
        id=uuid4(),
        scope_type="account",
        level="entry_halted",
        reason_code="consecutive_loss_limit",
        status="active",
        halted_at=datetime(2026, 10, 8, 1, tzinfo=UTC),
        released_at=None,
    )
    seen = {}

    def fake(db, workspace_id, bot, **kwargs):
        seen.update(kwargs)
        return decision_history.DecisionPage(
            items=[entry],
            next_before=(entry.signal.created_at, entry.signal.id),
            summary=decision_history.DecisionSummary(
                evaluations=5, counts={"hold": 4, "denied": 1}
            ),
            halts=[halt],
        )

    monkeypatch.setattr(decision_history, "list_decisions", fake)
    bot_id = uuid4()

    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/bots/{bot_id}/decisions",
        params={"outcome": ["denied", "halted"], "limit": 20, "from_time": "2026-10-01T00:00:00Z"},
    )

    assert response.status_code == 200
    body = response.json()
    (item,) = body["items"]
    assert (item["action"], item["outcome"], item["risk_reason_code"]) == (
        "sell",
        "denied",
        "hard_limit_breach",
    )
    assert item["failed_checks"] == [{"name": "binance_no_short", "detail": {}}]
    assert item["rationale"]["close"] == "82757"
    assert body["summary"] == {"evaluations": 5, "counts": {"hold": 4, "denied": 1}}
    assert body["halts"][0]["reason_code"] == "consecutive_loss_limit"
    assert body["next_before_id"] == str(entry.signal.id)
    assert seen["outcomes"] == ["denied", "halted"] and seen["limit"] == 20


def test_an_order_and_its_fill_are_returned_for_an_entry_that_was_placed(monkeypatch) -> None:
    order_id = uuid4()
    entry = _entry(
        outcome="ordered",
        risk_outcome="allow",
        risk_reason_code=None,
        failed_checks=[],
        order_id=order_id,
        order_side="buy",
        fill_price=Decimal("795.43"),
        fill_quantity=Decimal("20.953"),
    )
    monkeypatch.setattr(
        decision_history,
        "list_decisions",
        lambda db, workspace_id, bot, **kwargs: decision_history.DecisionPage(
            items=[entry],
            next_before=None,
            summary=decision_history.DecisionSummary(evaluations=1, counts={}),
            halts=[],
        ),
    )

    (item,) = client.get(f"/api/v1/workspaces/{WORKSPACE}/bots/{uuid4()}/decisions").json()["items"]

    assert item["order_id"] == str(order_id) and item["order_side"] == "buy"
    assert Decimal(item["fill_price"]) == Decimal("795.43")


def test_a_bot_of_another_workspace_or_a_missing_bot_is_not_found(_auth) -> None:
    _auth.get.return_value = TradingBot(id=uuid4(), workspace_id=uuid4(), name="theirs")
    assert client.get(f"/api/v1/workspaces/{WORKSPACE}/bots/{uuid4()}/decisions").status_code == 404

    _auth.get.return_value = None
    assert client.get(f"/api/v1/workspaces/{WORKSPACE}/bots/{uuid4()}/decisions").status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"before": "2026-10-07T00:00:00Z"},
        {"before_id": str(uuid4())},
        {"from_time": "2026-10-08T00:00:00Z", "to_time": "2026-10-07T00:00:00Z"},
        {"outcome": "maybe"},
        {"limit": 0},
        {"limit": 1000},
    ],
)
def test_rejects_requests_that_make_no_sense(params) -> None:
    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/bots/{uuid4()}/decisions", params=params)

    assert response.status_code == 422
