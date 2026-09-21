from uuid import UUID

from pydantic import BaseModel

from app.schemas.base import OrmModel


class AuthenticatedUserRead(OrmModel):
    id: UUID
    email: str
    display_name: str
    status: str


class WorkspaceMembershipRead(BaseModel):
    workspace_id: UUID
    workspace_name: str
    role: str


class CurrentUserRead(BaseModel):
    user: AuthenticatedUserRead
    memberships: list[WorkspaceMembershipRead]
