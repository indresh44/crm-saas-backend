"""Add follow-up, invoice, payment activity type enum values

Revision ID: 0024_activity_type_enums
Revises: 0023_sac_code_customer_address
Create Date: 2026-04-08 00:00:00
"""

from alembic import op

revision = "0024_activity_type_enums"
down_revision = "0023_sac_code_customer_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'followup_scheduled'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'followup_rescheduled'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'followup_completed'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'followup_cancelled'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'invoice_created'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'invoice_approved'")
    op.execute("ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS 'payment_recorded'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op
    pass
