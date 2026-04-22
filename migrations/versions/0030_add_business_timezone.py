"""Add timezone column to businesses

Revision ID: 0030_business_timezone
Revises: 0029_password_reset_tokens
Create Date: 2026-04-22 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_business_timezone"
down_revision = "0029_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every existing row backfills to Asia/Kolkata via the server_default.
    op.add_column(
        "businesses",
        sa.Column(
            "timezone",
            sa.String(length=50),
            nullable=False,
            server_default="Asia/Kolkata",
        ),
    )


def downgrade() -> None:
    op.drop_column("businesses", "timezone")
