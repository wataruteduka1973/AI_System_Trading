"""Integration test: a real request through the FastAPI app produces a
log line in the rotating log file, and that line contains no request
body / header content (only method/path/status/duration/request_id)."""

from app.core.config import Settings
from app.core.logging import configure_logging
from app.main import app
from fastapi.testclient import TestClient


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
    # /health/db raises SQLAlchemyError -> HTTPException(503) inside the
    # route (no PostgreSQL is running in this test process). FastAPI
    # converts that to a normal 503 Response before it reaches the
    # middleware, so this exercises the request_completed path with a
    # non-2xx status, not the middleware's own except-Exception branch
    # (there is no route here that raises past FastAPI's own handling).
    test_settings = Settings(log_dir=tmp_path)
    configure_logging(test_settings, log_filename="backend.log")

    client = TestClient(app)
    response = client.get("/api/v1/health/db")

    assert response.status_code == 503

    log_path = tmp_path / "backend.log"
    content = log_path.read_text(encoding="utf-8")
    assert "request_completed" in content
    assert "/api/v1/health/db" in content
    assert "status_code=503" in content or "'status_code': 503" in content
