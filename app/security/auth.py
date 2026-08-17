import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_owner(x_owner_token: Annotated[str | None, Header()] = None) -> str:
    """Validate the development-only owner token without logging its value."""
    configured = settings.dev_owner_token
    if configured is None or configured.get_secret_value() in {"", "change-me"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Development owner authentication is not configured",
        )
    if x_owner_token is None or not secrets.compare_digest(
        x_owner_token, configured.get_secret_value()
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid owner token")
    return "development-owner"
