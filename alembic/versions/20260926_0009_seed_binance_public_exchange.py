"""Seed the "binance_public" catalog entry for real-market backtesting research.

Revision ID: 20260926_0009
Revises: 20260921_0008
Create Date: 2026-09-26

Not a tradeable exchange: no `ExchangeConnection` ever targets it, and it is
never selectable for a `Bot`. It exists only so `Instrument` rows fed by
`app/exchanges/binance_public.py` (real Binance production klines, fetched
read-only for backtest research -- see that module's docstring) can live under
their own `exchange_id`, keeping them structurally distinct from the
`binance`-Testnet `BTCJPY` instrument actually used for paper execution
(same nominal symbol, unrelated price series -- Testnet liquidity is too thin
to reflect real market behavior).
"""

from alembic import op

revision = "20260926_0009"
down_revision = "20260921_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO fx.exchange (code, name, status)
        VALUES ('binance_public', 'Binance (公開履歴・検証専用)', 'active')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM fx.exchange AS exchange
        WHERE exchange.code = 'binance_public'
          AND NOT EXISTS (
              SELECT 1 FROM fx.instrument AS instrument WHERE instrument.exchange_id = exchange.id
          )
        """
    )
