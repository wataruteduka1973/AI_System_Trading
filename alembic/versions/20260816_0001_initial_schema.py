"""Create the initial FX trading schema.

Revision ID: 20260816_0001
Revises:
Create Date: 2026-08-16
"""

from pathlib import Path

from alembic import op

revision = "20260816_0001"
down_revision = None
branch_labels = None
depends_on = None


def _schema_sql() -> str:
    ddl_path = Path(__file__).resolve().parents[2] / "database" / "postgresql_schema_v0.1.sql"
    sql = ddl_path.read_text(encoding="utf-8-sig").strip()
    if sql.upper().startswith("BEGIN;"):
        sql = sql[len("BEGIN;") :].lstrip()
    if sql.upper().endswith("COMMIT;"):
        sql = sql[: -len("COMMIT;")].rstrip()
    return sql


def upgrade() -> None:
    """Apply the reviewed PostgreSQL DDL as the initial migration."""
    connection = op.get_bind().connection
    with connection.cursor() as cursor:
        cursor.execute(_schema_sql(), prepare=False)


def downgrade() -> None:
    """Remove the application schema created by this initial migration."""
    op.execute("DROP SCHEMA IF EXISTS fx CASCADE")
