"""Add preferred_language to businesses table

Revision ID: 0026_preferred_language
Revises: 0025_onboarding_fields
Create Date: 2026-04-09 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_preferred_language"
down_revision = "0025_onboarding_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("preferred_language", sa.String(length=20), nullable=False, server_default="hinglish"),
    )


def downgrade() -> None:
    op.drop_column("businesses", "preferred_language")
