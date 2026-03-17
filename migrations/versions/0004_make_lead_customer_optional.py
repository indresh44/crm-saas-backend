"""make lead customer optional

Revision ID: 0004_make_lead_customer_optional
Revises: 0003_whatsapp_tables
Create Date: 2026-03-16 00:00:00
"""

from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0004_make_lead_customer_optional"
down_revision = "0003_whatsapp_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "leads",
        "customer_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "leads",
        "customer_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
