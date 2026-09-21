from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.connections import ExchangeConnection

SCHEMA = settings.database_schema


class Workspace(Base):
    __tablename__ = "workspace"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connections: Mapped[list["ExchangeConnection"]] = relationship(back_populates="workspace")
    memberships: Mapped[list["UserMembership"]] = relationship(back_populates="workspace")


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    oidc_subject: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["UserMembership"]] = relationship(back_populates="user")


class UserMembership(Base):
    """Horizon5 Group A / Unit 1 (docs/plans/horizon5-implementation-plan.md).
    Maps a table that has existed since the initial migration
    (`database/postgresql_schema_v0.1.sql` 46-52行目) but had no ORM model until
    now -- RBAC (`app/security/rbac.py`) is the first consumer."""

    __tablename__ = "user_membership"
    __table_args__ = {"schema": SCHEMA}

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="memberships")
    user: Mapped["AppUser"] = relationship(back_populates="memberships")


class SessionRevocation(Base):
    """Horizon5 Group A / Unit 3.9 (docs/plans/horizon5-implementation-plan.md,
    added in response to a critical review of the plan: without this, a
    suspected leaked session has no faster remedy than waiting out
    `session_ttl_seconds`). One row per user; `revoked_sessions_before` is a
    cutoff -- any session JWT whose `iat` predates it is rejected by
    `app.security.rbac.require_authenticated_user`. New migration (this table
    does not exist in `postgresql_schema_v0.1.sql`)."""

    __tablename__ = "session_revocation"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="CASCADE"), primary_key=True
    )
    revoked_sessions_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
