"""Finish workspace connection and account management.

Revision ID: 20260818_0003
Revises: 20260817_0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260818_0003"
down_revision: str | None = "20260817_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE fx.exchange_connection
          ADD COLUMN IF NOT EXISTS credentials_updated_at timestamptz,
          ADD COLUMN IF NOT EXISTS verification_outcome text NOT NULL DEFAULT 'not_verified';

        DO $$ BEGIN
          ALTER TABLE fx.exchange_connection
            ADD CONSTRAINT ck_connection_verification_outcome CHECK (
              verification_outcome IN (
                'not_verified', 'success', 'authentication_failed', 'communication_failed'
              )
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;

        UPDATE fx.exchange_connection
           SET credentials_updated_at = COALESCE(credentials_updated_at, created_at)
         WHERE secret_ref IS NOT NULL;

        UPDATE fx.exchange_connection
           SET verification_outcome = CASE
             WHEN status = 'verified' THEN 'success'
             WHEN status = 'invalid' THEN 'authentication_failed'
             ELSE 'not_verified'
           END
         WHERE verification_outcome = 'not_verified';

        CREATE TABLE IF NOT EXISTS fx.workspace_account_selection (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id uuid NOT NULL REFERENCES fx.workspace(id) ON DELETE CASCADE,
          exchange_id uuid NOT NULL REFERENCES fx.exchange(id) ON DELETE RESTRICT,
          external_account_id uuid NOT NULL REFERENCES fx.external_account(id) ON DELETE CASCADE,
          selected_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CONSTRAINT uq_workspace_exchange_selection UNIQUE (workspace_id, exchange_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_selected_account
          ON fx.workspace_account_selection (workspace_id, external_account_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS fx.workspace_account_selection;
        ALTER TABLE fx.exchange_connection
          DROP CONSTRAINT IF EXISTS ck_connection_verification_outcome,
          DROP COLUMN IF EXISTS verification_outcome,
          DROP COLUMN IF EXISTS credentials_updated_at;
        """
    )
