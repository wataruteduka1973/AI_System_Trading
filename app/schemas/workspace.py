from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import OrmModel


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceRead(OrmModel):
    id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
