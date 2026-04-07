"""sac code + customer address and gstin

Revision ID: 0023_sac_code_customer_address
Revises: 0022_invoice_status_approved_lead_source
Create Date: 2026-04-07 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_sac_code_customer_address"
down_revision = "0022_status_lead_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Fix 2: SAC code ────────────────────────────────────────────────────
    op.add_column("catalog_items", sa.Column("sac_code", sa.String(length=20), nullable=True))
    op.add_column("invoice_items", sa.Column("sac_code", sa.String(length=20), nullable=True))
    op.add_column("businesses", sa.Column("default_sac_code", sa.String(length=20), nullable=True))

    # ── Fix 3: Customer address + GSTIN ───────────────────────────────────
    op.add_column("customers", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column("customers", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("customers", sa.Column("state", sa.String(length=100), nullable=True))
    op.add_column("customers", sa.Column("gst_number", sa.String(length=15), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "gst_number")
    op.drop_column("customers", "state")
    op.drop_column("customers", "city")
    op.drop_column("customers", "address")

    op.drop_column("businesses", "default_sac_code")
    op.drop_column("invoice_items", "sac_code")
    op.drop_column("catalog_items", "sac_code")
