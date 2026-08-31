"""Add durable worker storage without changing legacy job/subscription state."""

from alembic import op

revision = "20260831_0005"
down_revision = "20260825_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE fx.market_data_lease (
          workspace_id uuid NOT NULL REFERENCES fx.workspace(id) ON DELETE CASCADE,
          instrument_id uuid NOT NULL REFERENCES fx.instrument(id) ON DELETE CASCADE,
          timeframe text NOT NULL CHECK (
            timeframe IN ('1m', '5m', '15m', '30m', '1h', '4h', '1d')
          ),
          owner_id uuid,
          lease_token uuid,
          lease_until timestamptz,
          heartbeat_at timestamptz,
          work_kind text CHECK (work_kind IN ('backfill', 'polling')),
          work_id uuid,
          PRIMARY KEY (workspace_id, instrument_id, timeframe),
          CONSTRAINT ck_market_data_lease_owner CHECK (
            num_nonnulls(owner_id, lease_token, lease_until, heartbeat_at, work_kind, work_id)
            IN (0, 6)
          ),
          CONSTRAINT ck_market_data_lease_time CHECK (lease_until > heartbeat_at)
        );
        CREATE INDEX ix_market_data_lease_expiry ON fx.market_data_lease (lease_until)
          WHERE lease_token IS NOT NULL;
        ALTER TABLE fx.backfill_job
          ADD COLUMN next_fetch_at timestamptz,
          ADD COLUMN next_run_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ADD COLUMN consecutive_failures integer NOT NULL DEFAULT 0,
          ADD COLUMN progress_report jsonb NOT NULL DEFAULT '{}'::jsonb,
          ADD CONSTRAINT ck_backfill_checkpoint CHECK (
            next_fetch_at IS NULL OR next_fetch_at BETWEEN from_time AND to_time
          ),
          ADD CONSTRAINT ck_backfill_failures CHECK (consecutive_failures >= 0),
          ADD CONSTRAINT ck_backfill_progress_object CHECK (
            jsonb_typeof(progress_report) = 'object'
          );
        CREATE INDEX ix_backfill_due ON fx.backfill_job (next_run_at, created_at, id)
          WHERE status IN ('queued', 'running', 'validating');
        CREATE INDEX ix_backfill_active_range
          ON fx.backfill_job (workspace_id, instrument_id, timeframe, from_time, to_time)
          WHERE status IN ('queued', 'running', 'validating');
        ALTER TABLE fx.market_data_subscription
          ADD COLUMN next_fetch_at timestamptz,
          ADD COLUMN scan_to timestamptz,
          ADD COLUMN next_run_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ADD COLUMN consecutive_failures integer NOT NULL DEFAULT 0,
          ADD COLUMN blocked_reason text,
          ADD CONSTRAINT ck_subscription_checkpoint CHECK (
            (next_fetch_at IS NULL AND scan_to IS NULL) OR
            (next_fetch_at IS NOT NULL AND scan_to IS NOT NULL AND next_fetch_at <= scan_to)
          ),
          ADD CONSTRAINT ck_subscription_failures CHECK (consecutive_failures >= 0),
          ADD CONSTRAINT ck_subscription_blocked CHECK (
            blocked_reason IS NULL OR length(trim(blocked_reason)) > 0
          );
        UPDATE fx.market_data_subscription
          SET next_run_at = COALESCE(
            last_polled_at + poll_interval_seconds * INTERVAL '1 second', CURRENT_TIMESTAMP
          );
        CREATE INDEX ix_subscription_due ON fx.market_data_subscription (next_run_at, id)
          WHERE enabled = true AND blocked_reason IS NULL;
        """
    )


def downgrade() -> None:
    # Explicit operator action only: checkpoint metadata is lost, market data is retained.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        DROP INDEX fx.ix_subscription_due;
        ALTER TABLE fx.market_data_subscription
          DROP CONSTRAINT ck_subscription_checkpoint,
          DROP CONSTRAINT ck_subscription_failures,
          DROP CONSTRAINT ck_subscription_blocked,
          DROP COLUMN next_fetch_at, DROP COLUMN scan_to, DROP COLUMN next_run_at,
          DROP COLUMN consecutive_failures, DROP COLUMN blocked_reason;
        DROP INDEX fx.ix_backfill_due;
        DROP INDEX fx.ix_backfill_active_range;
        ALTER TABLE fx.backfill_job
          DROP CONSTRAINT ck_backfill_checkpoint, DROP CONSTRAINT ck_backfill_failures,
          DROP CONSTRAINT ck_backfill_progress_object,
          DROP COLUMN next_fetch_at, DROP COLUMN next_run_at,
          DROP COLUMN consecutive_failures, DROP COLUMN progress_report;
        DROP TABLE fx.market_data_lease;
        """
    )
