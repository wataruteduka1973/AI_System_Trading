"""Add fx.instrument_spread: latest observed bid/ask per instrument.

A "current state" table (one row per instrument, continuously UPSERTed), not an
append-only tick log -- matching `market_data_lease`'s (20260831_0005) precedent of a
domain-column primary key with no surrogate UUID id, since this table only ever needs
the latest observation. Fed from the live OANDA PricingStream connection that
`app/market_data/infrastructure/oanda_stream.py` already holds open (ticks arrive at up
to ~4/sec per OANDA's documented streaming cadence -- see
docs/concept/FXtrading_rebuild/07_公式仕様の確認記録.md's OANDA section), so writes are
frequent but the table itself never grows past one row per instrument: an UPSERT keyed
by `instrument_id` has no row-growth cost, unlike appending a new row per tick would.
Used by `app/trading/application/order_flow.py`'s fill simulator to compute
`expected_slippage` from a real spread instead of the previous hardcoded 0.
"""

from alembic import op

revision = "20260920_0007"
down_revision = "20260920_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE fx.instrument_spread (
          instrument_id uuid PRIMARY KEY REFERENCES fx.instrument(id) ON DELETE CASCADE,
          bid numeric(38, 18) NOT NULL CHECK (bid > 0),
          ask numeric(38, 18) NOT NULL CHECK (ask > 0),
          observed_at timestamptz NOT NULL,
          source text NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CONSTRAINT ck_instrument_spread_bid_ask CHECK (ask >= bid)
        );
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE fx.instrument_spread;")
