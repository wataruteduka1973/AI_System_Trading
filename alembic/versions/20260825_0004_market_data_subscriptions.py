"""Add persistent market-data subscriptions.

Revision ID: 20260825_0004
Revises: 20260818_0003
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260825_0004"
down_revision: str | None = "20260818_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fx.market_data_subscription (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id uuid NOT NULL REFERENCES fx.workspace(id) ON DELETE CASCADE,
          instrument_id uuid NOT NULL REFERENCES fx.instrument(id) ON DELETE CASCADE,
          timeframe text NOT NULL CHECK (
            timeframe IN ('1m', '5m', '15m', '30m', '1h', '4h', '1d')
          ),
          enabled boolean NOT NULL DEFAULT false,
          poll_interval_seconds integer NOT NULL DEFAULT 60
            CHECK (poll_interval_seconds BETWEEN 60 AND 3600),
          last_polled_at timestamptz,
          last_success_at timestamptz,
          last_error_code text,
          created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CONSTRAINT uq_market_data_subscription UNIQUE (
            workspace_id, instrument_id, timeframe
          )
        );
        CREATE INDEX IF NOT EXISTS ix_market_data_subscription_enabled
          ON fx.market_data_subscription (enabled, last_polled_at)
          WHERE enabled = true;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fx.market_data_subscription")
