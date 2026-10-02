"""Add fx.trading_position.stop_price: the stop-loss level of an open position.

Paper-trading bots whose strategy version uses `exit_policy: stop_loss`
(docs/plans/paper-trading-live-data.md Unit 3) set it when they open a position
from flat, at the fill price minus the Risk Gate's `stop_distance`, and close
the position once a later candle's low reaches it -- the same rule the
backtest applies. NULL means "no stop": every existing row, every position
opened by a `signal`-only strategy, and every manually placed order. Adding a
nullable column with no default rewrites no rows and loses no data.
"""

from alembic import op

revision = "20261002_0010"
down_revision = "20260926_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER TABLE fx.trading_position
          ADD COLUMN stop_price numeric(38, 18) CHECK (stop_price IS NULL OR stop_price > 0);
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE fx.trading_position DROP COLUMN stop_price;")
