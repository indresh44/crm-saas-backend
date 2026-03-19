"""add catalog trace fields to quote and invoice items

Revision ID: 0012_item_catalog_fields
Revises: 0011_catalog_items
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "0012_item_catalog_fields"
down_revision = "0011_catalog_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    invoice_item_columns = {column["name"] for column in inspector.get_columns("invoice_items")}
    quote_item_columns = {column["name"] for column in inspector.get_columns("quote_items")}

    if "name" not in invoice_item_columns:
        op.add_column(
            "invoice_items",
            sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        )
    if "unit" not in invoice_item_columns:
        op.add_column(
            "invoice_items",
            sa.Column("unit", sa.String(length=50), nullable=False, server_default="piece"),
        )
    if "catalog_item_id" not in invoice_item_columns:
        op.add_column(
            "invoice_items",
            sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(
            op.f("ix_invoice_items_catalog_item_id"),
            "invoice_items",
            ["catalog_item_id"],
            unique=False,
        )
        op.create_foreign_key(
            "fk_invoice_items_catalog_item_id_catalog_items",
            "invoice_items",
            "catalog_items",
            ["catalog_item_id"],
            ["id"],
        )

    if "name" not in quote_item_columns:
        op.add_column(
            "quote_items",
            sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        )
    if "unit" not in quote_item_columns:
        op.add_column(
            "quote_items",
            sa.Column("unit", sa.String(length=50), nullable=False, server_default="piece"),
        )
    if "catalog_item_id" not in quote_item_columns:
        op.add_column(
            "quote_items",
            sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(
            op.f("ix_quote_items_catalog_item_id"),
            "quote_items",
            ["catalog_item_id"],
            unique=False,
        )
        op.create_foreign_key(
            "fk_quote_items_catalog_item_id_catalog_items",
            "quote_items",
            "catalog_items",
            ["catalog_item_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    invoice_item_columns = {column["name"] for column in inspector.get_columns("invoice_items")}
    quote_item_columns = {column["name"] for column in inspector.get_columns("quote_items")}

    if "catalog_item_id" in invoice_item_columns:
        op.drop_constraint("fk_invoice_items_catalog_item_id_catalog_items", "invoice_items", type_="foreignkey")
        op.drop_index(op.f("ix_invoice_items_catalog_item_id"), table_name="invoice_items")
        op.drop_column("invoice_items", "catalog_item_id")
    if "unit" in invoice_item_columns:
        op.drop_column("invoice_items", "unit")
    if "name" in invoice_item_columns:
        op.drop_column("invoice_items", "name")

    if "catalog_item_id" in quote_item_columns:
        op.drop_constraint("fk_quote_items_catalog_item_id_catalog_items", "quote_items", type_="foreignkey")
        op.drop_index(op.f("ix_quote_items_catalog_item_id"), table_name="quote_items")
        op.drop_column("quote_items", "catalog_item_id")
    if "unit" in quote_item_columns:
        op.drop_column("quote_items", "unit")
    if "name" in quote_item_columns:
        op.drop_column("quote_items", "name")
