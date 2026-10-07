"""Add fx.instrument_spread_history: observed bid/ask over time.

`fx.instrument_spread` keeps only the latest bid/ask per instrument, so a backtest can only
use one spread for a whole run (ADR 0003's known approximation). No public source serves
past order-book quotes, so the only way to have a real spread for a past period is to
store the observations as they arrive. This table is that record: an append-only,
sampled series (at most one row per instrument per 30 seconds -- see
`app/market_data/application/spread_tracking.py`), written next to the latest-value upsert.

Primary key (instrument_id, observed_at): ordered lookups by instrument and time need no
extra index. Rows are not pruned; at one row per 30 s a symbol adds ~2,900 rows a day.
Adding a new table loses no data.
"""

from alembic import op

revision = "20261007_0011"
down_revision = "20261002_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE fx.instrument_spread_history (
          instrument_id uuid NOT NULL REFERENCES fx.instrument(id) ON DELETE CASCADE,
          observed_at timestamptz NOT NULL,
          bid numeric(38, 18) NOT NULL CHECK (bid > 0),
          ask numeric(38, 18) NOT NULL CHECK (ask > 0),
          source text NOT NULL,
          PRIMARY KEY (instrument_id, observed_at),
          CONSTRAINT ck_instrument_spread_history_bid_ask CHECK (ask >= bid)
        );
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE fx.instrument_spread_history;")
