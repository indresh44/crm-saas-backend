"""add pdf url and generated timestamp to invoices

Revision ID: 0019_invoice_pdf_fields
Revises: 0018_business_settings
Create Date: 2026-03-20 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_invoice_pdf_fields"
down_revision = "0018_business_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("pdf_url", sa.String(length=500), nullable=True))
    op.add_column("invoices", sa.Column("pdf_generated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "pdf_generated_at")
    op.drop_column("invoices", "pdf_url")
