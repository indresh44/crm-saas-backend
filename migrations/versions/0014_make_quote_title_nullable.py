"""make quote title nullable

Revision ID: 0014_quote_title_null
Revises: 0013_invoice_quote_id
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_quote_title_null"
down_revision = "0013_invoice_quote_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "quotes",
        "title",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE quotes SET title = '' WHERE title IS NULL")
    op.alter_column(
        "quotes",
        "title",
        existing_type=sa.String(),
        nullable=False,
    )
