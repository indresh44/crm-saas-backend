"""Add welcome_email_sent_at to businesses table

Revision ID: 0035_welcome_email_sent_at
Revises: 0034_payment_void_and_lineage
Create Date: 2026-04-26 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_welcome_email_sent_at"
down_revision = "0034_payment_void_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("welcome_email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("businesses", "welcome_email_sent_at")
