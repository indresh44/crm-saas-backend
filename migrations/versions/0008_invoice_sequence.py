"""add invoice sequence per business

Revision ID: 0008_invoice_sequence
Revises: 0007_one_pipeline_per_business
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_invoice_sequence"
down_revision = "0007_one_pipeline_per_business"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("invoice_sequence", sa.Integer(), nullable=False, server_default="0"),
    )

    op.execute(
        """
        UPDATE businesses
        SET invoice_sequence = (
            SELECT COUNT(*)
            FROM invoices
            WHERE invoices.business_id = businesses.id
        )
        """
    )

    op.execute(
        """
        UPDATE invoices
        SET invoice_number = sub.new_number
        FROM (
            SELECT
                id,
                'INV-' || LPAD(
                    ROW_NUMBER() OVER (
                        PARTITION BY business_id
                        ORDER BY created_at ASC
                    )::text,
                    3, '0'
                ) AS new_number
            FROM invoices
        ) sub
        WHERE invoices.id = sub.id
          AND (invoices.invoice_number IS NULL OR invoices.invoice_number = '')
        """
    )


def downgrade() -> None:
    op.drop_column("businesses", "invoice_sequence")
