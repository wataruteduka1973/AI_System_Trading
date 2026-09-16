from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import configure_logging, configure_named_log_file
from app.main import app
from app.security.auth import require_owner

client = TestClient(app)


def override_owner() -> None:
    app.dependency_overrides[require_owner] = lambda: "test-owner"


def configure_test_logging(tmp_path) -> None:
    test_settings = Settings(log_dir=tmp_path)
    configure_logging(test_settings)
    configure_named_log_file(test_settings, logger_name="app.client", log_filename="frontend.log")


def test_accepted_entry_is_written_to_frontend_log(tmp_path) -> None:
    configure_test_logging(tmp_path)
    override_owner()
    try:
        response = client.post(
            "/api/v1/client-logs",
            json={
                "message": "TypeError: cannot read properties of undefined",
                "stack": "at HealthPanel (HealthPanel.tsx:12)",
                "source": "error-boundary",
                "path": "/workspaces/abc/connections",
                "user_agent": "Mozilla/5.0",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202

    log_path = tmp_path / "frontend.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "frontend_error" in content
    assert "cannot read properties of undefined" in content
    assert "/workspaces/abc/connections" in content


def test_unwhitelisted_fields_are_ignored(tmp_path) -> None:
    configure_test_logging(tmp_path)
    override_owner()
    try:
        response = client.post(
            "/api/v1/client-logs",
            json={
                "message": "boom",
                "source": "window.onerror",
                "path": "/",
                # Not part of ClientLogEntry; must be silently dropped, never logged.
                "api_key": "should-never-appear-in-any-log",
                "form_values": {"password": "should-never-appear-in-any-log"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202

    log_path = tmp_path / "frontend.log"
    content = log_path.read_text(encoding="utf-8")
    assert "should-never-appear-in-any-log" not in content


def test_oversized_message_is_rejected(tmp_path) -> None:
    configure_test_logging(tmp_path)
    override_owner()
    try:
        response = client.post(
            "/api/v1/client-logs",
            json={
                "message": "x" * 5000,  # exceeds ClientLogEntry's max_length=2000
                "source": "window.onerror",
                "path": "/",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_requires_owner_token() -> None:
    app.dependency_overrides.clear()
    response = client.post(
        "/api/v1/client-logs",
        json={"message": "boom", "source": "window.onerror", "path": "/"},
    )
    assert response.status_code in (401, 503)
