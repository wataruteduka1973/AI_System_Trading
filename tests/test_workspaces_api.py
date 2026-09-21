from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.workspace import AppUser, UserMembership, Workspace
from app.security.rbac import (
    require_authenticated_user,
    require_operator_role,
    require_owner_role,
    require_viewer_role,
)
from fastapi.testclient import TestClient

client = TestClient(app)

_TEST_USER = AppUser(
    id=uuid4(), email="test@example.com", display_name="Test User", status="active"
)


def override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_user] = lambda: _TEST_USER
    app.dependency_overrides[require_viewer_role] = lambda: _TEST_USER
    app.dependency_overrides[require_operator_role] = lambda: _TEST_USER
    app.dependency_overrides[require_owner_role] = lambda: _TEST_USER


def test_list_workspaces() -> None:
    now = datetime.now(UTC)
    workspace = Workspace(id=uuid4(), name="Personal", status="active")
    workspace.created_at = now
    workspace.updated_at = now
    session = MagicMock()
    session.scalars.return_value.all.return_value = [workspace]
    override_database(session)
    try:
        response = client.get("/api/v1/workspaces")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Personal"


def test_create_workspace_registers_the_creator_as_owner() -> None:
    """Unit 4.2 (docs/plans/horizon5-implementation-plan.md 715行目 テスト方針
    要求): creating a workspace must also insert a `user_membership` row
    making the creator its `owner` -- otherwise nobody could ever pass
    `Viewer`/`Operator`/`Owner` for a workspace they just created."""
    now = datetime.now(UTC)

    def set_generated_values(workspace: object) -> None:
        workspace.id = uuid4()
        workspace.status = "active"
        workspace.created_at = now
        workspace.updated_at = now

    session = MagicMock()
    session.refresh.side_effect = set_generated_values
    override_database(session)
    try:
        response = client.post("/api/v1/workspaces", json={"name": "Personal"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["name"] == "Personal"
    added = [call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(obj, Workspace) for obj in added)
    memberships = [obj for obj in added if isinstance(obj, UserMembership)]
    assert len(memberships) == 1
    assert memberships[0].role == "owner"
    assert memberships[0].user_id == _TEST_USER.id
    session.commit.assert_called_once()


def test_get_missing_workspace_returns_404() -> None:
    session = MagicMock()
    session.get.return_value = None
    override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
