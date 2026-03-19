"""forward-fix invoice sequence for upgraded databases

Revision ID: 0010_invoice_seq_fix
Revises: 0009_attachment_upload
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0010_invoice_seq_fix"
down_revision = "0009_attachment_upload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("businesses")}

    if "invoice_sequence" not in columns:
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
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("businesses")}

    if "invoice_sequence" in columns:
        op.drop_column("businesses", "invoice_sequence")
