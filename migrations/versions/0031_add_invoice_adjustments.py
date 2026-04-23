"""Add invoice_adjustments table

Revision ID: 0031_invoice_adjustments
Revises: 0030_business_timezone
Create Date: 2026-04-23 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0031_invoice_adjustments"
down_revision = "0030_business_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_adjustments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("adjustment_type", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_invoice_adjustments_amount_positive"),
        sa.CheckConstraint(
            "adjustment_type IN ('discount', 'write_off')",
            name="ck_invoice_adjustments_type",
        ),
    )
    op.create_index(
        "ix_invoice_adjustments_invoice_id",
        "invoice_adjustments",
        ["invoice_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_adjustments_invoice_id", table_name="invoice_adjustments")
    op.drop_table("invoice_adjustments")
