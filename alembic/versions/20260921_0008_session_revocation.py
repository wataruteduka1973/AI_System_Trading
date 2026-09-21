"""Add fx.session_revocation: per-user cutoff timestamp for forced session
invalidation (Horizon5 Group A, Unit 3.9 -- docs/plans/horizon5-implementation-plan.md).

Without this, a suspected leaked session/credential has no faster remedy than
waiting out `session_ttl_seconds` (the roadmap's own Horizon5 completion
condition requires session security tests to pass, which a non-revocable
session JWT cannot satisfy). One row per user (not one row per session/jti):
`revoked_sessions_before` is a cutoff timestamp, checked against a session
JWT's `iat` claim by `app.security.rbac.require_authenticated_user` -- any
session issued before the cutoff is rejected. This is coarser than per-device
revocation (revoking one leaked session logs out every other device the user
has open too) but needs no per-session storage.
"""

from alembic import op

revision = "20260921_0008"
down_revision = "20260920_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE fx.session_revocation (
          user_id uuid PRIMARY KEY REFERENCES fx.app_user(id) ON DELETE CASCADE,
          revoked_sessions_before timestamptz NOT NULL,
          revoked_by uuid REFERENCES fx.app_user(id) ON DELETE SET NULL,
          revoked_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE fx.session_revocation;")
