"""add catalog items table

Revision ID: 0011_catalog_items
Revises: 0010_invoice_seq_fix
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_catalog_items"
down_revision = "0010_invoice_seq_fix"
branch_labels = None
depends_on = None


catalog_item_unit = postgresql.ENUM(
    "piece",
    "sq_ft",
    "meter",
    "kg",
    "hour",
    "session",
    "month",
    "trip",
    "lot",
    "custom",
    name="catalog_item_unit",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    catalog_item_unit.create(bind, checkfirst=True)

    op.create_table(
        "catalog_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("unit", catalog_item_unit, nullable=False),
        sa.Column("custom_unit", sa.String(length=50), nullable=True),
        sa.Column("default_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("gst_percent", sa.Numeric(5, 2), nullable=False, server_default="18.00"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_items_business_id", "catalog_items", ["business_id"], unique=False)
    op.create_index("ix_catalog_items_business_name", "catalog_items", ["business_id", "name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_catalog_items_business_name", table_name="catalog_items")
    op.drop_index("ix_catalog_items_business_id", table_name="catalog_items")
    op.drop_table("catalog_items")
    catalog_item_unit.drop(op.get_bind(), checkfirst=True)
