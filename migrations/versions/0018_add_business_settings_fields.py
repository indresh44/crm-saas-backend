"""add business profile and invoice settings fields

Revision ID: 0018_business_settings
Revises: 0017_auth_tables
Create Date: 2026-03-20 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_business_settings"
down_revision = "0017_auth_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("businesses", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column("businesses", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("businesses", sa.Column("state", sa.String(length=100), nullable=True))
    op.add_column("businesses", sa.Column("pin_code", sa.String(length=10), nullable=True))
    op.add_column("businesses", sa.Column("logo_url", sa.String(length=500), nullable=True))
    op.add_column("businesses", sa.Column("gst_number", sa.String(length=15), nullable=True))
    op.add_column(
        "businesses",
        sa.Column("gst_mode", sa.String(length=20), nullable=True, server_default="exclusive"),
    )
    op.add_column(
        "businesses",
        sa.Column("invoice_prefix", sa.String(length=10), nullable=True, server_default="INV"),
    )
    op.add_column(
        "businesses",
        sa.Column("default_due_days", sa.Integer(), nullable=True, server_default="15"),
    )
    op.add_column("businesses", sa.Column("bank_name", sa.String(length=200), nullable=True))
    op.add_column(
        "businesses",
        sa.Column("bank_account_number", sa.String(length=50), nullable=True),
    )
    op.add_column("businesses", sa.Column("bank_ifsc", sa.String(length=20), nullable=True))
    op.add_column("businesses", sa.Column("upi_id", sa.String(length=100), nullable=True))
    op.add_column("businesses", sa.Column("invoice_notes", sa.String(length=500), nullable=True))
    op.add_column("businesses", sa.Column("invoice_footer", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "invoice_footer")
    op.drop_column("businesses", "invoice_notes")
    op.drop_column("businesses", "upi_id")
    op.drop_column("businesses", "bank_ifsc")
    op.drop_column("businesses", "bank_account_number")
    op.drop_column("businesses", "bank_name")
    op.drop_column("businesses", "default_due_days")
    op.drop_column("businesses", "invoice_prefix")
    op.drop_column("businesses", "gst_mode")
    op.drop_column("businesses", "gst_number")
    op.drop_column("businesses", "logo_url")
    op.drop_column("businesses", "pin_code")
    op.drop_column("businesses", "state")
    op.drop_column("businesses", "city")
    op.drop_column("businesses", "address")
    op.drop_column("businesses", "email")
