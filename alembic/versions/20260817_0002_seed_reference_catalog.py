"""Seed supported exchanges and markets.

Revision ID: 20260817_0002
Revises: 20260816_0001
Create Date: 2026-08-17
"""

from alembic import op

revision = "20260817_0002"
down_revision = "20260816_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO fx.exchange (code, name, status)
        VALUES
            ('oanda', 'OANDA', 'active'),
            ('binance', 'Binance', 'active')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name
        """
    )
    op.execute(
        """
        INSERT INTO fx.market (code, asset_class, product_type, settlement_type)
        VALUES
            ('foreign_fx_spot', 'foreign_fx', 'fx_spot', 'rolling_spot'),
            ('crypto_spot', 'crypto', 'spot', 'physical')
        ON CONFLICT (code) DO UPDATE
        SET asset_class = EXCLUDED.asset_class,
            product_type = EXCLUDED.product_type,
            settlement_type = EXCLUDED.settlement_type
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM fx.market AS market
        WHERE market.code IN ('foreign_fx_spot', 'crypto_spot')
          AND NOT EXISTS (
              SELECT 1 FROM fx.instrument AS instrument WHERE instrument.market_id = market.id
          )
        """
    )
    op.execute(
        """
        DELETE FROM fx.exchange AS exchange
        WHERE exchange.code IN ('oanda', 'binance')
          AND NOT EXISTS (
              SELECT 1 FROM fx.exchange_connection AS connection
              WHERE connection.exchange_id = exchange.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM fx.instrument AS instrument WHERE instrument.exchange_id = exchange.id
          )
        """
    )
