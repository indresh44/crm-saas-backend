"""Add invoice cancellation fields

Adds `cancelled_at` and `cancelled_reason` audit columns on invoices,
and extends the Postgres `invoice_status` enum type with `cancelled`.

Revision ID: 0032_invoice_cancellation
Revises: 0031_invoice_adjustments
Create Date: 2026-04-24 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_invoice_cancellation"
down_revision = "0031_invoice_adjustments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres stores `invoices.status` as a native enum type, so we have to
    # teach the DB about the new value before any row can take it. IF NOT
    # EXISTS makes the migration safe to re-run.
    op.execute("ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'cancelled'")

    op.add_column(
        "invoices",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "cancelled_reason")
    op.drop_column("invoices", "cancelled_at")
    # Postgres does not support removing values from an enum type. Leaving
    # 'cancelled' in the type on downgrade is the conventional tradeoff.
