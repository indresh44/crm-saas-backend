"""Add onboarding fields to businesses table

Revision ID: 0025_onboarding_fields
Revises: 0024_activity_type_enums
Create Date: 2026-04-09 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_onboarding_fields"
down_revision = "0024_activity_type_enums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("is_whatsapp", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("businesses", sa.Column("business_type", sa.String(length=50), nullable=True))
    op.add_column("businesses", sa.Column("business_type_label", sa.String(length=100), nullable=True))
    op.add_column("businesses", sa.Column("onboarding_status", sa.String(length=20), nullable=False, server_default="pending"))
    op.add_column("businesses", sa.Column("onboarding_method", sa.String(length=10), nullable=True))

    # Set existing businesses as onboarding completed (they were created before onboarding existed)
    op.execute("UPDATE businesses SET onboarding_status = 'completed' WHERE onboarding_status = 'pending'")


def downgrade() -> None:
    op.drop_column("businesses", "onboarding_method")
    op.drop_column("businesses", "onboarding_status")
    op.drop_column("businesses", "business_type_label")
    op.drop_column("businesses", "business_type")
    op.drop_column("businesses", "is_whatsapp")
