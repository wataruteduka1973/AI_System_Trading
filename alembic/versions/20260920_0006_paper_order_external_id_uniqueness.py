"""Allow multiple paper trade_order rows with external_order_id=NULL per account.

`uq_trade_order_external_id` was declared `NULLS NOT DISTINCT`, which treats every
external_order_id=NULL row for the same account as a duplicate. Self-simulated paper
orders (FR-ORD-15: paper accounts never call a real exchange endpoint) never receive an
external_order_id, so with the original constraint a paper account could hold at most one
trade_order ever -- confirmed against the live DB while implementing the Horizon 3 order
flow (app/trading/application/order_flow.py). Reverting to the standard NULLS DISTINCT
behavior (multiple NULLs are not considered duplicates) fixes paper orders while still
enforcing uniqueness once a live order's real external_order_id is populated.
"""

from alembic import op

revision = "20260920_0006"
down_revision = "20260831_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER TABLE fx.trade_order
          DROP CONSTRAINT uq_trade_order_external_id,
          ADD CONSTRAINT uq_trade_order_external_id UNIQUE (account_id, external_order_id);
        """
    )


def downgrade() -> None:
    # Explicit operator action only: fails if more than one NULL external_order_id row
    # already exists per account (expected once any paper order has been placed).
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER TABLE fx.trade_order
          DROP CONSTRAINT uq_trade_order_external_id,
          ADD CONSTRAINT uq_trade_order_external_id
            UNIQUE NULLS NOT DISTINCT (account_id, external_order_id);
        """
    )
