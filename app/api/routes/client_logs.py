"""Relay endpoint for frontend-captured errors.

The browser cannot write to a local file directly, so the frontend POSTs
whitelisted error fields here and this endpoint writes them into
logs/frontend.log via the same structured-logging setup the backend uses
(see app/core/logging.py). Nothing here is a general-purpose logging
sink: only the fields in ClientLogEntry are accepted, and none of them
may carry request bodies, form values, or credentials.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status

from app.schemas.client_logs import ClientLogEntry
from app.security.auth import require_owner

router = APIRouter()
Owner = Annotated[str, Depends(require_owner)]

client_logger = structlog.get_logger("app.client")


@router.post("/client-logs", status_code=status.HTTP_202_ACCEPTED, tags=["observability"])
def report_client_log(entry: ClientLogEntry, _owner: Owner) -> dict[str, str]:
    client_logger.error(
        "frontend_error",
        message=entry.message,
        stack=entry.stack,
        source=entry.source,
        path=entry.path,
        user_agent=entry.user_agent,
    )
    return {"status": "accepted"}
