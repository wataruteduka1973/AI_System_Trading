"""Unit 1 (docs/plans/horizon5-implementation-plan.md): ORM mapping for
UserMembership/SessionRevocation. No DB connection is used, matching this
codebase's existing model tests (e.g. test_model_metadata.py,
test_backtest_models.py): `configure_mappers()` resolves every ForeignKey
string against the shared `Base.metadata`, which is enough to catch an
unresolved target without a live database.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.session import Base
from app.models import AppUser, SessionRevocation, UserMembership, Workspace
from sqlalchemy.orm import configure_mappers


def test_user_membership_and_session_revocation_tables_are_registered() -> None:
    configure_mappers()

    assert UserMembership.__table__ is Base.metadata.tables["fx.user_membership"]
    assert SessionRevocation.__table__ is Base.metadata.tables["fx.session_revocation"]
    assert UserMembership.__table__.c.workspace_id.foreign_keys
    assert UserMembership.__table__.c.user_id.foreign_keys
    assert SessionRevocation.__table__.c.user_id.foreign_keys
    assert SessionRevocation.__table__.c.revoked_by.foreign_keys


def _workspace(**overrides: object) -> Workspace:
    defaults: dict[str, object] = dict(id=uuid4(), name="Acme", status="active")
    defaults.update(overrides)
    return Workspace(**defaults)


def _user(**overrides: object) -> AppUser:
    defaults: dict[str, object] = dict(
        id=uuid4(), email="a@example.com", display_name="A", status="active"
    )
    defaults.update(overrides)
    return AppUser(**defaults)


def test_user_membership_relates_to_workspace_and_user_both_ways() -> None:
    workspace = _workspace()
    user = _user()
    membership = UserMembership(workspace_id=workspace.id, user_id=user.id, role="owner")
    membership.workspace = workspace
    membership.user = user

    assert membership.workspace is workspace
    assert membership.user is user
    assert membership in workspace.memberships
    assert membership in user.memberships


def test_session_revocation_construction() -> None:
    user = _user()
    owner = _user()
    now = datetime.now(UTC)
    revocation = SessionRevocation(
        user_id=user.id, revoked_sessions_before=now, revoked_by=owner.id
    )
    assert revocation.user_id == user.id
    assert revocation.revoked_sessions_before == now
    assert revocation.revoked_by == owner.id
