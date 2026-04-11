"""Add package view support - deliverables and attachment ordering

Revision ID: 0027_package_view
Revises: 0026_preferred_language
Create Date: 2026-04-11 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "0027_package_view"
down_revision = "0026_preferred_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add sort_order and is_primary to attachments
    op.add_column(
        "attachments",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "attachments",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 2. Add 'invoice_item' to attachment_entity_type enum
    op.execute("ALTER TYPE attachment_entity_type ADD VALUE IF NOT EXISTS 'invoice_item'")

    # 3. Add deliverables (JSONB) to catalog_items
    op.add_column(
        "catalog_items",
        sa.Column("deliverables", JSONB(), nullable=True),
    )

    # 4. Add deliverables (JSONB) to invoice_items
    op.add_column(
        "invoice_items",
        sa.Column("deliverables", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoice_items", "deliverables")
    op.drop_column("catalog_items", "deliverables")
    op.drop_column("attachments", "is_primary")
    op.drop_column("attachments", "sort_order")
    # Note: Cannot remove enum value 'invoice_item' from PostgreSQL enum
