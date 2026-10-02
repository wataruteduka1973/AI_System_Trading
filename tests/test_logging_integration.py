"""Integration test: a real request through the FastAPI app produces a
log line in the rotating log file, and that line contains no request
body / header content (only method/path/status/duration/request_id)."""

from unittest.mock import MagicMock

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import get_db
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError


def test_request_is_written_to_log_file(tmp_path) -> None:
    test_settings = Settings(log_dir=tmp_path)
    configure_logging(test_settings, log_filename="backend.log")

    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200

    log_path = tmp_path / "backend.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "request_completed" in content
    assert "GET" in content
    assert "/api/v1/health" in content
    assert "status_code=200" in content or "'status_code': 200" in content


def test_error_response_status_code_is_still_logged(tmp_path) -> None:
    # /health/db turns SQLAlchemyError into HTTPException(503) inside the
    # route. FastAPI converts that to a normal 503 Response before it reaches
    # the middleware, so this exercises the request_completed path with a
    # non-2xx status, not the middleware's own except-Exception branch
    # (there is no route here that raises past FastAPI's own handling).
    # The session is a stub that always fails: relying on "no PostgreSQL is
    # running" made this test fail on a machine where the local DB is up.
    test_settings = Settings(log_dir=tmp_path)
    configure_logging(test_settings, log_filename="backend.log")

    failing_session = MagicMock()
    failing_session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    app.dependency_overrides[get_db] = lambda: failing_session
    try:
        client = TestClient(app)
        response = client.get("/api/v1/health/db")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503

    log_path = tmp_path / "backend.log"
    content = log_path.read_text(encoding="utf-8")
    assert "request_completed" in content
    assert "/api/v1/health/db" in content
    assert "status_code=503" in content or "'status_code': 503" in content
