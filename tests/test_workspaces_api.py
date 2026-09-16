from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.session import get_db
from app.main import app
from app.models.workspace import Workspace
from app.security.auth import require_owner
from fastapi.testclient import TestClient

client = TestClient(app)


def override_database(session: MagicMock) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_owner] = lambda: "test-owner"


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


def test_get_missing_workspace_returns_404() -> None:
    session = MagicMock()
    session.get.return_value = None
    override_database(session)
    try:
        response = client.get(f"/api/v1/workspaces/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
