from pydantic import BaseModel, Field


class ClientLogEntry(BaseModel):
    """One frontend-captured error report.

    Deliberately narrow: only these whitelisted, non-sensitive fields are
    accepted. This is not a general-purpose logging sink -- the frontend
    must never send request payloads, form field values, or anything
    that could carry exchange credentials. See
    docs/plans/observability-logging.md for the design rationale.
    """

    message: str = Field(max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)
    source: str = Field(
        max_length=200,
        description='e.g. "window.onerror", "unhandledrejection", "error-boundary"',
    )
    path: str = Field(max_length=500, description="Page path where the error occurred")
    user_agent: str | None = Field(default=None, max_length=300)
