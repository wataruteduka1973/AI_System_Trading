import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, configure_named_log_file
from app.db.session import get_db

configure_logging(settings)
configure_named_log_file(settings, logger_name="app.client", log_filename="frontend.log")
request_logger = structlog.get_logger("app.request")

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.middleware("http")
async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log method/path/status/duration for every request.

    Deliberately never logs headers or the request/response body: those
    can carry exchange credentials (see connections endpoints) or session
    data. See app/core/logging.py for the redaction processor that backs
    this up for any other log call in the codebase.
    """
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        request_logger.exception(
            "request_failed",
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        raise
    finally:
        structlog.contextvars.clear_contextvars()
    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    request_logger.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.get(f"{settings.api_v1_prefix}/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@app.get(f"{settings.api_v1_prefix}/health/db", tags=["health"])
def database_health_check(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unavailable",
                "database": "postgresql",
                "message": "Check DATABASE_URL and PostgreSQL availability.",
            },
        ) from exc
    return {"status": "ok", "database": "postgresql"}
