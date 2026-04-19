"""Add invoice_templates and invoice_template_items

Revision ID: 0028_invoice_templates
Revises: 0027_package_view
Create Date: 2026-04-19 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision = "0028_invoice_templates"
down_revision = "0027_package_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column(
            "source_invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_invoice_templates_business_id",
        "invoice_templates",
        ["business_id"],
    )
    op.create_index(
        "ix_invoice_templates_business_name",
        "invoice_templates",
        ["business_id", "name"],
    )

    op.create_table(
        "invoice_template_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoice_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "catalog_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("catalog_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False, server_default="piece"),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("gst_percent", sa.Numeric(5, 2), nullable=False, server_default="0.0"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("sac_code", sa.String(length=20), nullable=True),
        sa.Column("deliverables", JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "gst_percent IN (0, 5, 12, 18, 28)",
            name="ck_invoice_template_items_gst_percent",
        ),
    )
    op.create_index(
        "ix_invoice_template_items_template_id",
        "invoice_template_items",
        ["template_id"],
    )
    op.create_index(
        "ix_invoice_template_items_catalog_item_id",
        "invoice_template_items",
        ["catalog_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_template_items_catalog_item_id", table_name="invoice_template_items")
    op.drop_index("ix_invoice_template_items_template_id", table_name="invoice_template_items")
    op.drop_table("invoice_template_items")
    op.drop_index("ix_invoice_templates_business_name", table_name="invoice_templates")
    op.drop_index("ix_invoice_templates_business_id", table_name="invoice_templates")
    op.drop_table("invoice_templates")
