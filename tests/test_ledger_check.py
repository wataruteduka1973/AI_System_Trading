"""The periodic ledger check and the release gate (app/trading/application/ledger_check.py,
api/routes/trading_halts.py, docs/plans/ledger-reconciliation.md)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.db.session import get_db
from app.main import app
from app.models.audit import OutboxEvent, SystemEvent
from app.models.strategy import TradingHalt
from app.models.workspace import AppUser
from app.security.rbac import require_owner_role, require_viewer_role
from app.trading.application import ledger_check
from app.trading.application import ledger_reconciliation as lr
from fastapi.testclient import TestClient

WORKSPACE, ACCOUNT = uuid4(), uuid4()
FINDING = lr.Finding("position_quantity_mismatch", "建玉の数量が合いません", {"fills": "10"})


def _db(accounts):
    db = MagicMock()
    db.execute.return_value.all.return_value = accounts
    db.scalar.return_value = None  # no previous release, no active halt
    return db


@pytest.fixture
def reconcile(monkeypatch):
    results = {}
    monkeypatch.setattr(lr, "reconcile_account", lambda db, account_id: results.get(account_id, []))
    return results


def _added(db, kind):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], kind)]


def test_a_mismatch_stops_the_account_and_announces_it(reconcile) -> None:
    reconcile[ACCOUNT] = [FINDING]
    db = _db([(WORKSPACE, ACCOUNT)])

    assert ledger_check.check_ledger_reconciliation(db) == 1

    (halt,) = _added(db, TradingHalt)
    assert (halt.scope_type, halt.scope_id, halt.level, halt.reason_code) == (
        "account",
        ACCOUNT,
        "emergency_stopped",
        "ledger_mismatch",
    )
    assert halt.auto_releasable is False
    (event,) = _added(db, SystemEvent)
    assert event.severity == "critical"
    assert "建玉の数量が合いません" in event.message
    assert event.payload["finding_count"] == 1
    assert event.payload["findings"][0]["code"] == "position_quantity_mismatch"
    assert len(_added(db, OutboxEvent)) == 1
    db.commit.assert_called_once_with()


def test_an_account_that_reconciles_is_left_alone(reconcile) -> None:
    db = _db([(WORKSPACE, ACCOUNT)])

    assert ledger_check.check_ledger_reconciliation(db) == 0

    assert _added(db, TradingHalt) == []
    db.commit.assert_not_called()


def test_one_failing_account_does_not_stop_the_others(monkeypatch) -> None:
    other = uuid4()

    def fake(db, account_id):
        if account_id == ACCOUNT:
            raise RuntimeError("boom")
        return [FINDING]

    monkeypatch.setattr(lr, "reconcile_account", fake)
    db = _db([(WORKSPACE, ACCOUNT), (WORKSPACE, other)])

    assert ledger_check.check_ledger_reconciliation(db) == 1

    db.rollback.assert_called()
    (halt,) = _added(db, TradingHalt)
    assert halt.scope_id == other


# --- the Owner's release ---------------------------------------------------------------------

client = TestClient(app)
_OWNER = AppUser(id=uuid4(), email="o@example.com", display_name="Owner", status="active")


def _halt(**overrides):
    defaults = dict(
        id=uuid4(),
        workspace_id=WORKSPACE,
        scope_type="account",
        scope_id=ACCOUNT,
        level="emergency_stopped",
        reason_code="ledger_mismatch",
        status="active",
        halted_at=datetime.now(UTC),
        released_at=None,
    )
    defaults.update(overrides)
    return TradingHalt(**defaults)


def _release(halt):
    session = MagicMock()
    session.scalar.return_value = halt
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _OWNER
    app.dependency_overrides[require_owner_role] = lambda: _OWNER
    try:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE}/trading-halts/{halt.id}/emergency-release"
        )
    finally:
        app.dependency_overrides.clear()
    return response, session


def test_the_release_is_refused_while_the_ledger_still_disagrees(reconcile) -> None:
    reconcile[ACCOUNT] = [FINDING]
    halt = _halt()

    response, session = _release(halt)

    assert response.status_code == 409
    assert "建玉の数量が合いません" in response.json()["detail"]
    assert halt.status == "active"
    session.commit.assert_not_called()


def test_the_release_goes_through_once_the_ledger_reconciles(reconcile) -> None:
    halt = _halt()

    response, session = _release(halt)

    assert response.status_code == 200
    assert halt.status == "released" and halt.released_by == _OWNER.id
    session.commit.assert_called_once_with()


def test_other_emergency_stops_are_released_without_a_reconciliation(reconcile) -> None:
    reconcile[ACCOUNT] = [FINDING]  # would block a ledger halt; a user's stop does not care
    halt = _halt(reason_code="user_emergency_stop", scope_type="workspace", scope_id=None)

    response, _ = _release(halt)

    assert response.status_code == 200
