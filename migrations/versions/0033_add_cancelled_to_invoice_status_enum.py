"""Add 'cancelled' value to the invoice_status Postgres enum

Follow-up to 0032 for environments that already applied 0032 before the
`ALTER TYPE` statement was added to it. IF NOT EXISTS makes this a no-op
for fresh installs that ran the corrected 0032.

Revision ID: 0033_cancelled_enum_value
Revises: 0032_invoice_cancellation
Create Date: 2026-04-24 00:00:00
"""

from alembic import op

revision = "0033_cancelled_enum_value"
down_revision = "0032_invoice_cancellation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # Postgres does not support removing values from an enum type.
    pass
