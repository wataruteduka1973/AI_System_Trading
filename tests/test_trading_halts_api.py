"""Unit 4.4 (docs/plans/horizon5-implementation-plan.md): trading_halt
management API. `trading_halt.py`'s own state-machine tests
(tests/test_trading_halt.py) already cover activate/escalate/deescalate in
depth; this file is about the HTTP layer wired on top of it (role gating,
id-based routing, the emergency_stopped split between the two release
endpoints).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.strategy import TradingHalt
from app.models.workspace import AppUser
from app.security.rbac import require_owner_role, require_viewer_role
from fastapi.testclient import TestClient

client = TestClient(app)
_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def _override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    app.dependency_overrides[require_owner_role] = lambda: _TEST_USER


def _halt(**overrides: object) -> TradingHalt:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        workspace_id=uuid4(),
        scope_type="bot",
        scope_id=uuid4(),
        level="entry_halted",
        reason_code="data_delay",
        status="active",
        halted_at=datetime.now(UTC),
        released_at=None,
    )
    defaults.update(overrides)
    return TradingHalt(**defaults)


def test_list_active_trading_halts() -> None:
    workspace_id = uuid4()
    halt = _halt(workspace_id=workspace_id)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [halt]
    _override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/trading-halts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["reason_code"] == "data_delay"


def test_release_returns_404_for_a_halt_in_a_different_workspace() -> None:
    session = MagicMock()
    session.scalar.return_value = None  # _get_halt's workspace-scoped lookup
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{uuid4()}/trading-halts/{uuid4()}/release")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_release_returns_404_for_an_already_released_halt() -> None:
    """Regression test (Group A self-review): _get_halt used to fetch a halt
    by id/workspace_id alone, without checking status == "active". Retrying
    /release on an already-released halt reached deescalate_one_step's
    `_find_active_halt`, found nothing, returned None, and hit the route's
    `assert updated is not None` -- an uncaught AssertionError instead of a
    clean 4xx."""
    workspace_id = uuid4()
    session = MagicMock()
    # _get_halt's own query now filters status == "active", so an
    # already-released halt is indistinguishable from a missing one.
    session.scalar.return_value = None
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/trading-halts/{uuid4()}/release")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_release_rejects_an_emergency_stopped_halt() -> None:
    workspace_id = uuid4()
    halt = _halt(workspace_id=workspace_id, level="emergency_stopped")
    session = MagicMock()
    session.scalar.return_value = halt
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/trading-halts/{halt.id}/release")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_release_steps_an_entry_halted_halt_down_to_warning() -> None:
    workspace_id = uuid4()
    halt = _halt(workspace_id=workspace_id, level="entry_halted")
    session = MagicMock()
    # _get_halt's lookup, then trading_halt.deescalate_one_step's own
    # _find_active_halt lookup (same row, re-queried by the application module).
    session.scalar.side_effect = [halt, halt]
    _override_database(session)
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/trading-halts/{halt.id}/release")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["level"] == "warning"


def test_emergency_release_releases_an_emergency_stopped_halt() -> None:
    workspace_id = uuid4()
    halt = _halt(workspace_id=workspace_id, level="emergency_stopped")
    session = MagicMock()
    session.scalar.return_value = halt
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/trading-halts/{halt.id}/emergency-release"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "released"


def test_emergency_release_rejects_a_non_emergency_halt() -> None:
    workspace_id = uuid4()
    halt = _halt(workspace_id=workspace_id, level="entry_halted")
    session = MagicMock()
    session.scalar.return_value = halt
    _override_database(session)
    try:
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/trading-halts/{halt.id}/emergency-release"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_release_requires_owner_role() -> None:
    workspace_id, halt_id = uuid4(), uuid4()
    session = MagicMock()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    # require_owner_role intentionally left un-overridden: with no session
    # cookie, it falls through to require_authenticated_user's real 401.
    try:
        response = client.post(f"/api/v1/workspaces/{workspace_id}/trading-halts/{halt_id}/release")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
