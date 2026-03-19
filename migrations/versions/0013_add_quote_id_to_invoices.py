"""add quote_id to invoices table

Revision ID: 0013_invoice_quote_id
Revises: 0012_item_catalog_fields
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "0013_invoice_quote_id"
down_revision = "0012_item_catalog_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    invoice_columns = {column["name"] for column in inspector.get_columns("invoices")}

    if "quote_id" not in invoice_columns:
        op.add_column(
            "invoices",
            sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(op.f("ix_invoices_quote_id"), "invoices", ["quote_id"], unique=False)
        op.create_foreign_key(
            "fk_invoices_quote_id_quotes",
            "invoices",
            "quotes",
            ["quote_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    invoice_columns = {column["name"] for column in inspector.get_columns("invoices")}

    if "quote_id" in invoice_columns:
        op.drop_constraint("fk_invoices_quote_id_quotes", "invoices", type_="foreignkey")
        op.drop_index(op.f("ix_invoices_quote_id"), table_name="invoices")
        op.drop_column("invoices", "quote_id")
